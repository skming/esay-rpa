from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core import storage
from app.models.schemas import ScrapeResult
from app.services.runtime_variables import RuntimeVariableStore

type FlowNode = dict[str, object]

_SCRIPT_ACTION_TYPES = {"script.python", "script.javascript", "script.shell", "script.websocket"}
_MAX_STDIO_BYTES = 512_000  # 单路输出上限，防止失控脚本把日志/内存打爆
_MAX_ENV_BYTES = 64_000  # 多数系统 env 总量上限留出安全余量，超出则改用紧凑/省略副本
_MAX_ENV_VALUE_BYTES = 8_000  # 单个变量塞进紧凑 JSON 副本前的上限，超出的改走 RPA_VARIABLES_FILE

# 注入到内联 Python 脚本开头，确保 _vars 读取未截断的文件快照而非可能被截断的
# env var 副本；sentinel 防止重复注入。
_PY_PREAMBLE_SENTINEL = "# __rpa_vars_injected__"
_PY_PREAMBLE = """\
# __rpa_vars_injected__
import json as __j, os as __o
__rf = __o.environ.get('RPA_VARIABLES_FILE', '')
_vars = __j.load(open(__rf, encoding='utf-8')) if __rf and __o.path.exists(__rf) else __j.loads(__o.environ.get('RPA_VARIABLES_JSON', '{}'))
del __j, __o, __rf
"""

# AI/UI 生成的旧版加载样板，须清除，否则会用可能截断的数据覆盖注入的 _vars。
_PY_STALE_LOADERS = [
    "_vars = json.loads(os.environ.get('RPA_VARIABLES_JSON', '{}'))",
    (
        "_rf = os.environ.get('RPA_VARIABLES_FILE', '')\n"
        "_vars = json.load(open(_rf, encoding='utf-8')) if _rf and os.path.exists(_rf) "
        "else json.loads(os.environ.get('RPA_VARIABLES_JSON', '{}'))"
    ),
]


@dataclass(frozen=True)
class ScriptActionResult:
    action_type: str
    command: list[str]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str

    def to_scrape_result(self) -> ScrapeResult:
        values = [self.stdout] if self.stdout else []
        if self.stderr:
            values.append(self.stderr)
        return ScrapeResult(
            url=self.cwd,
            selector=f"{self.action_type} exit={self.exit_code}",
            count=len(values),
            values=values,
        )


class ScriptActionRunner:
    def __init__(self, workspace_root: Path | None = None) -> None:
        self._workspace_root = (workspace_root or _resolve_workspace_root()).resolve()

    async def run(self, node: FlowNode, variables: RuntimeVariableStore, *, timeout_ms: int) -> ScriptActionResult:
        action_type = _read_action_type(node)
        if action_type == "script.shell":
            return await self._run_shell(node, variables, timeout_ms=timeout_ms)
        if action_type == "script.websocket":
            return await self._run_websocket(node, variables, timeout_ms=timeout_ms)
        script_path = self._resolve_script_path(node, variables)
        command = _build_command(action_type, script_path)
        timeout_seconds = max(1, timeout_ms) / 1000
        # python/js 走固定解释器，PATH 收窄到系统目录即可；script.shell 需要用户自定义命令，PATH 不收窄（见 _run_shell）
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self._workspace_root,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_build_script_env(variables, restrict_path=True),
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise TimeoutError(f"脚本执行超时: {script_path.name}") from exc

        stdout = _decode_limited(stdout_bytes, "stdout")
        stderr = _decode_limited(stderr_bytes, "stderr")
        return ScriptActionResult(
            action_type=action_type,
            command=command,
            cwd=str(self._workspace_root),
            exit_code=process.returncode or 0,
            stdout=stdout,
            stderr=stderr,
        )

    async def _run_shell(self, node: FlowNode, variables: RuntimeVariableStore, *, timeout_ms: int) -> ScriptActionResult:
        command = _read_optional_string(node, "command") or _read_optional_string(node, "path") or ""
        if not command:
            raise ValueError("script.shell 节点缺少 command")
        command = _resolve_shell_safe(command, variables)
        timeout_seconds = max(1, timeout_ms) / 1000
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=self._workspace_root,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_build_script_env(variables, restrict_path=False),
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise TimeoutError("Shell 命令执行超时") from exc
        return ScriptActionResult(
            action_type="script.shell",
            command=[command],
            cwd=str(self._workspace_root),
            exit_code=process.returncode or 0,
            stdout=_decode_limited(stdout_bytes, "stdout"),
            stderr=_decode_limited(stderr_bytes, "stderr"),
        )

    async def _run_websocket(self, node: FlowNode, variables: RuntimeVariableStore, *, timeout_ms: int) -> ScriptActionResult:
        import aiohttp
        url = _read_optional_string(node, "url")
        if not url:
            raise ValueError("script.websocket 节点缺少 url")
        url = variables.resolve_text(url)
        message = _read_optional_string(node, "message") or ""
        if message:
            message = variables.resolve_text(message)
        timeout_seconds = max(1, timeout_ms) / 1000
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as ws:
                    if message:
                        await ws.send_str(message)
                    resp = await asyncio.wait_for(ws.receive(), timeout=timeout_seconds)
                    data = str(resp.data) if resp.data is not None else ""
        except aiohttp.WSServerHandshakeError as exc:
            raise RuntimeError(f"WebSocket 握手失败: {exc}") from exc
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"WebSocket 等待超时: {url}") from exc
        return ScriptActionResult(
            action_type="script.websocket",
            command=[url],
            cwd="",
            exit_code=0,
            stdout=data,
            stderr="",
        )

    def _resolve_script_path(self, node: FlowNode, variables: RuntimeVariableStore) -> Path:
        raw_path = _read_optional_string(node, "path") or _read_optional_string(node, "scriptPath") or _read_optional_string(node, "filePath")
        raw_code = _read_optional_string(node, "code")

        if raw_path:
            rendered = variables.resolve_text(raw_path)
            path = Path(rendered)
            if path.is_absolute():
                raise ValueError("脚本节点只能使用相对路径")
            resolved = (self._workspace_root / path).resolve()
            if not resolved.is_relative_to(self._workspace_root):
                raise ValueError("脚本路径超出 RPA 工作目录")
            if not resolved.exists():
                if raw_code:
                    resolved.parent.mkdir(parents=True, exist_ok=True)
                    resolved.write_text(variables.resolve_text(raw_code), encoding="utf-8")
                else:
                    raise FileNotFoundError(f"脚本文件不存在: {path.name}")
            return resolved

        # code 模式（默认）：写入临时文件执行
        if not raw_code:
            raise ValueError("script 节点缺少 code 或 path 字段")
        rendered_code = variables.resolve_text(raw_code)
        action_type = node.get("type", "script.python")
        ext = ".py" if "python" in str(action_type) else ".js"
        if ext == ".py":
            rendered_code = _inject_py_preamble(rendered_code)
        code_hash = hashlib.md5(rendered_code.encode()).hexdigest()[:8]
        # 内联脚本是临时缓存而非用户产出，放专用缓存目录（不进用户工作区）并定期清理。
        tmp_dir = storage.temp_scripts_dir()
        storage.prune_temp_scripts()
        tmp_file = tmp_dir / f"inline_{code_hash}{ext}"
        tmp_file.write_text(rendered_code, encoding="utf-8")
        return tmp_file


def _inject_py_preamble(code: str) -> str:
    if _PY_PREAMBLE_SENTINEL in code:
        return code
    for stale in _PY_STALE_LOADERS:
        code = code.replace(stale, "")
    return _PY_PREAMBLE + code


def is_script_action_node(node: FlowNode) -> bool:
    return node.get("type") in _SCRIPT_ACTION_TYPES


def apply_script_result_variables(node: FlowNode, result: ScriptActionResult, variables: RuntimeVariableStore) -> list[str]:
    saved_names: list[str] = []
    output_variable = _read_optional_string(node, "outputVariable") or _read_optional_string(node, "responseVariable") or _read_optional_string(node, "resultVariable")
    if output_variable is not None:
        variables.set(output_variable, result.stdout, scope="局部")
        saved_names.append(output_variable)

    status_variable = _read_optional_string(node, "statusVariable") or _read_optional_string(node, "exitCodeVariable")
    if status_variable is not None:
        variables.set(status_variable, result.exit_code, scope="局部")
        saved_names.append(status_variable)

    stderr_variable = _read_optional_string(node, "stderrVariable")
    if stderr_variable is not None:
        variables.set(stderr_variable, result.stderr, scope="局部")
        saved_names.append(stderr_variable)
    return saved_names


def _build_command(action_type: str, script_path: Path) -> list[str]:
    if action_type == "script.python":
        if script_path.suffix.lower() != ".py":
            raise ValueError("Python 脚本节点仅允许执行 .py 文件")
        return [sys.executable, str(script_path)]
    if action_type == "script.javascript":
        if script_path.suffix.lower() not in {".js", ".mjs"}:
            raise ValueError("JavaScript 脚本节点仅允许执行 .js/.mjs 文件")
        node_path = shutil.which("node")
        if node_path is None:
            raise RuntimeError("系统未找到 node，无法执行 JavaScript 脚本节点")
        return [node_path, str(script_path)]
    raise ValueError(f"不支持的脚本节点类型: {action_type}")


def _build_variables_payload(variables: RuntimeVariableStore) -> str:
    payload = variables.raw_values()
    return _json_dumps(payload)


def _build_script_env(variables: RuntimeVariableStore, *, restrict_path: bool) -> dict[str, str]:
    """构造脚本子进程环境。

    打包态后端会通过 PYTHONPATH 暴露随包 site-packages；脚本节点必须继承它，
    否则 `script.python` 子进程会找不到 openpyxl 等内置依赖。完整变量快照始终
    写入 RPA_VARIABLES_FILE，确保脚本读到未截断的数据；env var 仅作紧凑副本。
    """
    env = dict(os.environ)
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin" if restrict_path else os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    env["HOME"] = str(Path.home())

    full_payload = _build_variables_payload(variables)
    variables_file = _write_variables_payload_file(full_payload)
    env["RPA_VARIABLES_FILE"] = str(variables_file)

    # 同时暴露紧凑副本给直接读 RPA_VARIABLES_JSON 的 shell 脚本/旧代码。
    if len(full_payload.encode("utf-8")) <= _MAX_ENV_BYTES:
        env["RPA_VARIABLES_JSON"] = full_payload
    else:
        env["RPA_VARIABLES_JSON"] = _build_compact_variables_payload(variables)
    return env


def _write_variables_payload_file(payload: str) -> Path:
    tmp_dir = storage.temp_scripts_dir()
    digest = hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]
    path = tmp_dir / f"variables_{digest}.json"
    if not path.exists():
        path.write_text(payload, encoding="utf-8")
    return path


def _build_compact_variables_payload(variables: RuntimeVariableStore) -> str:
    compact: dict[str, object] = {}
    for name, value in variables.raw_values().items():
        rendered = _json_dumps(value)
        if len(rendered.encode("utf-8")) <= _MAX_ENV_VALUE_BYTES:
            compact[name] = value
        else:
            compact[name] = {
                "__omitted__": True,
                "reason": "变量值过大，完整快照请读取 RPA_VARIABLES_FILE",
                "bytes": len(rendered.encode("utf-8")),
            }

    rendered_compact = _json_dumps(compact)
    if len(rendered_compact.encode("utf-8")) <= _MAX_ENV_BYTES:
        return rendered_compact
    return _json_dumps({
        "__omitted__": True,
        "reason": "变量快照过大，完整快照请读取 RPA_VARIABLES_FILE",
    })


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _decode_limited(value: bytes, name: str) -> str:
    if len(value) > _MAX_STDIO_BYTES:
        raise ValueError(f"脚本 {name} 输出超过 512KB")
    return value.decode("utf-8", errors="replace").strip()


def _read_action_type(node: FlowNode) -> str:
    value = node.get("type")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("脚本节点缺少 type")
    return value.strip()


def _read_required_string(node: FlowNode, key: str, *, fallback_keys: tuple[str, ...] = ()) -> str:
    for candidate_key in (key, *fallback_keys):
        value = node.get(candidate_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"脚本节点缺少 {key}")


def _read_optional_string(node: FlowNode, key: str) -> str | None:
    value = node.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _resolve_workspace_root() -> Path:
    return storage.resolve_workspace_root()


_SHELL_VAR_PATTERN = re.compile(r"\$\{var\.([A-Za-z_][A-Za-z0-9_.-]{0,119})\}")


def _resolve_shell_safe(template: str, variables: RuntimeVariableStore) -> str:
    """替换 ${var.X} 时用 shlex.quote() 转义，避免变量内容注入 shell 元字符。"""
    raw = variables.raw_values()

    def _quote(m: re.Match) -> str:
        name = m.group(1)
        val = raw.get(name)
        if val is None:
            return m.group(0)
        return shlex.quote(str(val) if not isinstance(val, str) else val)

    return _SHELL_VAR_PATTERN.sub(_quote, template)
