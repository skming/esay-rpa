from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from app.services.runtime_variables import RuntimeVariableStore
from app.services.script_action_runner import ScriptActionRunner, apply_script_result_variables
from app.services import script_action_runner


async def test_python_script_runner_writes_output_variables(tmp_path) -> None:
    script = tmp_path / "clean.py"
    script.write_text("import os\nprint(os.environ['RPA_VARIABLES_JSON'])\n", encoding="utf-8")
    variables = RuntimeVariableStore.from_initial({"order_id": "A001"})
    runner = ScriptActionRunner(tmp_path)
    node = {
        "type": "script.python",
        "path": "clean.py",
        "outputVariable": "script_stdout",
        "statusVariable": "script_exit_code",
        "stderrVariable": "script_stderr",
        "inputVariables": ["order_id"],
    }

    result = await runner.run(node, variables, timeout_ms=5_000)
    saved = apply_script_result_variables(node, result, variables)

    assert result.exit_code == 0
    assert "order_id" in result.stdout
    assert saved == ["script_stdout", "script_exit_code", "script_stderr"]
    assert variables.get("script_exit_code") == 0
    assert "A001" in str(variables.get("script_stdout"))


async def test_python_script_runner_accepts_response_variable_alias(tmp_path) -> None:
    script = tmp_path / "clean.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    variables = RuntimeVariableStore.from_initial({})
    runner = ScriptActionRunner(tmp_path)
    node = {"type": "script.python", "path": "clean.py", "responseVariable": "script_stdout"}

    result = await runner.run(node, variables, timeout_ms=5_000)
    saved = apply_script_result_variables(node, result, variables)

    assert saved == ["script_stdout"]
    assert variables.get("script_stdout").strip() == "ok"


async def test_script_runner_rejects_path_traversal(tmp_path) -> None:
    runner = ScriptActionRunner(tmp_path)
    variables = RuntimeVariableStore()

    with pytest.raises(ValueError, match="脚本路径超出 RPA 工作目录"):
        await runner.run({"type": "script.python", "path": "../escape.py"}, variables, timeout_ms=1_000)


async def test_python_script_runner_preserves_pythonpath(tmp_path, monkeypatch) -> None:
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    (module_dir / "custom_dependency.py").write_text("VALUE = 'from-pythonpath'\n", encoding="utf-8")
    script = tmp_path / "use_dependency.py"
    script.write_text("import custom_dependency\nprint(custom_dependency.VALUE)\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(module_dir))

    runner = ScriptActionRunner(tmp_path)
    result = await runner.run({"type": "script.python", "path": "use_dependency.py"}, RuntimeVariableStore(), timeout_ms=5_000)

    assert result.exit_code == 0
    assert result.stdout == "from-pythonpath"


async def test_python_script_runner_writes_large_variables_to_file(tmp_path) -> None:
    script = tmp_path / "read_large_variables.py"
    script.write_text(
        "import json, os\n"
        "payload_path = os.environ.get('RPA_VARIABLES_FILE')\n"
        "assert payload_path, 'RPA_VARIABLES_FILE missing'\n"
        "with open(payload_path, encoding='utf-8') as f:\n"
        "    data = json.load(f)\n"
        "print(len(data['large_payload']))\n",
        encoding="utf-8",
    )
    variables = RuntimeVariableStore.from_initial({"large_payload": "x" * 80_000})
    runner = ScriptActionRunner(tmp_path)

    result = await runner.run(
        {"type": "script.python", "path": "read_large_variables.py", "inputVariables": ["large_payload"]},
        variables,
        timeout_ms=5_000,
    )

    assert result.exit_code == 0
    assert result.stdout == "80000"


def _script_node(tmp_path, action_type: str, python_code: str, javascript_code: str) -> dict[str, str]:
    if action_type == "script.javascript":
        if shutil.which("node") is None:
            pytest.skip("Node.js is not installed")
        path = tmp_path / "check.js"
        path.write_text(javascript_code, encoding="utf-8")
    else:
        path = tmp_path / "check.py"
        path.write_text(python_code, encoding="utf-8")
    if action_type == "script.shell":
        args = [sys.executable, str(path)]
        command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
        return {"type": action_type, "command": command}
    return {"type": action_type, "path": path.name}


@pytest.fixture
def spawned_processes(monkeypatch):
    processes = []
    for name in ("create_subprocess_exec", "create_subprocess_shell"):
        original = getattr(asyncio, name)

        async def capture(*args, _original=original, **kwargs):
            process = await _original(*args, **kwargs)
            processes.append(process)
            return process

        monkeypatch.setattr(asyncio, name, capture)
    return processes


@pytest.mark.parametrize("action_type", ["script.python", "script.javascript", "script.shell"])
async def test_script_process_preserves_nonzero_exit_and_both_streams(tmp_path, action_type) -> None:
    node = _script_node(
        tmp_path, action_type,
        "import sys\nprint('out')\nprint('err', file=sys.stderr)\nsys.exit(7)\n",
        "console.log('out'); console.error('err'); process.exitCode = 7;",
    )

    result = await ScriptActionRunner(tmp_path).run(node, RuntimeVariableStore(), timeout_ms=5_000)

    assert (result.exit_code, result.stdout, result.stderr) == (7, "out", "err")


@pytest.mark.parametrize("action_type", ["script.python", "script.javascript", "script.shell"])
@pytest.mark.parametrize("interruption", ["cancel", "timeout"])
async def test_script_process_is_reaped_on_interruption(tmp_path, spawned_processes, action_type, interruption) -> None:
    node = _script_node(tmp_path, action_type, "import time\ntime.sleep(0.5)\n", "setTimeout(() => {}, 500);")
    job = asyncio.create_task(ScriptActionRunner(tmp_path).run(
        node, RuntimeVariableStore(), timeout_ms=100 if interruption == "timeout" else 5_000,
    ))
    if interruption == "cancel":
        async with asyncio.timeout(2):
            while not spawned_processes:
                await asyncio.sleep(0.005)
        job.cancel()
    with pytest.raises(asyncio.CancelledError if interruption == "cancel" else TimeoutError):
        await job

    assert spawned_processes[0].returncode is not None


@pytest.mark.parametrize("action_type", ["script.python", "script.javascript", "script.shell"])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
async def test_script_output_limit_stops_process_before_later_side_effect(tmp_path, action_type, stream) -> None:
    node = _script_node(
        tmp_path, action_type,
        f"import sys, time\nfrom pathlib import Path\nsys.{stream}.write('x' * 512001)\nsys.{stream}.flush()\ntime.sleep(0.3)\nPath('after_limit').touch()\n",
        f"require('fs').writeSync({1 if stream == 'stdout' else 2}, 'x'.repeat(512001)); setTimeout(() => require('fs').writeFileSync('after_limit', ''), 300);",
    )

    with pytest.raises(ValueError, match=f"脚本 {stream} 输出超过 512KB"):
        await ScriptActionRunner(tmp_path).run(node, RuntimeVariableStore(), timeout_ms=5_000)
    await asyncio.sleep(0.4)

    assert not (tmp_path / "after_limit").exists()


@pytest.mark.parametrize("action_type", ["script.python", "script.javascript", "script.shell"])
async def test_script_output_limit_accepts_exact_limit_on_both_streams(tmp_path, action_type) -> None:
    node = _script_node(
        tmp_path, action_type,
        "import sys\nsys.stdout.write('x' * 512000)\nsys.stderr.write('y' * 512000)\n",
        "require('fs').writeSync(1, 'x'.repeat(512000)); require('fs').writeSync(2, 'y'.repeat(512000));",
    )

    result = await ScriptActionRunner(tmp_path).run(node, RuntimeVariableStore(), timeout_ms=5_000)

    assert result.exit_code == 0
    assert result.stdout == "x" * 512000
    assert result.stderr == "y" * 512000


@pytest.mark.parametrize("interruption", ["cancel", "timeout"])
async def test_shell_interruption_stops_descendant_process(tmp_path, interruption) -> None:
    child = tmp_path / "child.py"
    child.write_text(
        "import time\nfrom pathlib import Path\nPath('child_started').touch()\ntime.sleep(0.7)\nPath('child_finished').touch()\n",
        encoding="utf-8",
    )
    node = _script_node(
        tmp_path, "script.shell",
        "import subprocess, sys\nsubprocess.run([sys.executable, 'child.py'], check=True)\n",
        "",
    )
    job = asyncio.create_task(ScriptActionRunner(tmp_path).run(
        node, RuntimeVariableStore(), timeout_ms=400 if interruption == "timeout" else 5_000,
    ))
    async with asyncio.timeout(2):
        while not (tmp_path / "child_started").exists():
            await asyncio.sleep(0.005)
    if interruption == "cancel":
        job.cancel()
    with pytest.raises(asyncio.CancelledError if interruption == "cancel" else TimeoutError):
        await job
    await asyncio.sleep(0.8)

    assert not (tmp_path / "child_finished").exists()


async def test_windows_cleanup_uses_native_process_tree_termination(monkeypatch) -> None:
    process = AsyncMock()
    process.pid = 1234
    process.returncode = None
    process.stdout.read.return_value = b""
    process.stderr.read.return_value = b""
    killer = AsyncMock()

    async def killed():
        process.returncode = 1
        return 0

    killer.wait.side_effect = killed
    spawn = AsyncMock(return_value=killer)
    monkeypatch.setattr(script_action_runner, "os", type("WindowsOS", (), {"name": "nt"}))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    await script_action_runner._terminate_process(process)

    assert spawn.call_args.args == ("taskkill", "/PID", "1234", "/T", "/F")
    process.wait.assert_awaited_once()
    process.kill.assert_not_called()


async def test_repeated_cancellation_waits_for_process_cleanup(tmp_path, spawned_processes, monkeypatch) -> None:
    cleaning = asyncio.Event()
    terminate = script_action_runner._terminate_process

    async def slow_cleanup(process):
        cleaning.set()
        await asyncio.sleep(0.05)
        await terminate(process)

    monkeypatch.setattr(script_action_runner, "_terminate_process", slow_cleanup)
    node = _script_node(tmp_path, "script.python", "import time\ntime.sleep(0.5)\n", "")
    job = asyncio.create_task(ScriptActionRunner(tmp_path).run(node, RuntimeVariableStore(), timeout_ms=5_000))
    async with asyncio.timeout(2):
        while not spawned_processes:
            await asyncio.sleep(0.005)
        job.cancel()
        await cleaning.wait()
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job

    assert spawned_processes[0].returncode is not None
