"""真实模型 + 真实执行器的端到端评测入口。

另两档各只证一半：test_real_page_flow_replay.py 证手写节点表能在真实浏览器上筛对数据，
run_evals.py 证模型在 mock 工具上的调用策略。这一档接上中间那段：真实模型 → 真实观察/
交互工具 → 模型自己保存流程 → 真实执行器运行 → 用本文件独立声明的预期数据裁决。
判据只看运行结束后落在 task 变量里的数据，不读页面 fixture、也不读流程定义。

    cd backend && python -m evals.run_e2e --self-check      # 不调模型：验证接线/隔离/判据
    cd backend && python -m evals.run_e2e                   # 真实模型端到端（需可用模型配置）
    cd backend && python -m evals.run_e2e --only pagination_sweep
    cd backend && python -m evals.run_e2e --root /tmp/rpa-e2e   # 指定隔离根目录

--self-check 走同一条执行器链路，但流程是手写的，**不计入模型端到端成功率**。

不导入 app.main：PLAYWRIGHT_BROWSERS_PATH 只在那里设置，隔离 RPA_APP_DATA_DIR 之后再
导入它，内核目录会指到空的 root 下，触发一次几百 MB 的重新下载。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from copy import deepcopy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import quote
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, get_args

# 允许 `python -m evals.run_e2e` 与直接执行两种方式
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import load_settings  # noqa: E402
from app.models.schemas import FlowStatus  # noqa: E402
from app.services.ai_config_service import AiConfigService  # noqa: E402
from app.services.ai_orchestrator import (  # noqa: E402
    AiOrchestrator, FlowState, GuardState, _detect_turn_intents, _tool_schemas_for_round,
)
from app.services.ai_tools.diagnostics import _parse_runtime_value  # noqa: E402
from app.services.ai_tools.executor import RpaToolExecutor  # noqa: E402
from app.services.ai_tools.schemas import TOOL_SCHEMAS  # noqa: E402
from app.services.log_broker import LogBroker  # noqa: E402
from app.services.runtime_factory import create_runtime_services  # noqa: E402
from evals.metrics import collect_run_metrics  # noqa: E402
from evals.run_evals import _observe_guards, _resolve_model_and_key  # noqa: E402

_PAGES = Path(__file__).resolve().parent.parent / "tests" / "pages"
# 模型手上真正有的工具。executor 还会收到平台自己发起的调用，代价指标不该把它们算进去。
_MODEL_FACING_TOOLS = frozenset(item["function"]["name"] for item in TOOL_SCHEMAS)


class _FixtureHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass


@contextmanager
def fixture_server() -> Iterator[str]:
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(_FixtureHandler, directory=str(_PAGES)),
    )
    worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def page_url(name: str, base_url: str) -> str:
    return f"{base_url}/{quote(name)}"


def isolation_env(root: Path) -> dict[str, str]:
    """把所有会写盘的路径挪到 root 下，返回待写入 os.environ 的映射。

    做成纯函数便于测试逐个 resolve_* 校验：storage.py 的 resolver 都在调用时读环境变量，
    漏一个键就有一类数据仍写进用户真实目录，而那种泄漏不会报错。
    DATABASE_URL 与 RPA_DATABASE_PATH 都要给：前者存在时 load_settings 直接用它，
    只设后者会被用户 shell 里已有的 DATABASE_URL 盖掉。
    RPA_AI_CONFIG_PATH 指到 root 是为了「只读用户配置、绝不写回」。
    """
    database = root / "db" / "rpa.sqlite3"
    return {
        "RPA_APP_DATA_DIR": str(root),
        "RPA_WORKSPACE_ROOT": str(root / "workspace"),
        "RPA_LOG_DIR": str(root / "logs"),
        "RPA_CACHE_DIR": str(root / "cache"),
        "RPA_DATABASE_PATH": str(database),
        "DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        "RPA_AI_CONFIG_PATH": str(root / "ai" / "config.json"),
    }


def prepare_root(root: Path) -> None:
    for sub in ("db", "workspace/runs", "logs", "cache", "runtime/browser", "ai/chats"):
        (root / sub).mkdir(parents=True, exist_ok=True)


def apply_isolation(root: Path) -> dict[str, str]:
    """只写环境变量、不建服务：storage 与 load_settings 都在调用时读环境。"""
    prepare_root(root)
    env = isolation_env(root)
    for key, value in env.items():
        os.environ[key] = value
    return env


@dataclass
class E2ECase:
    """一个端到端用例：一张页面 + 一句需求 + 多组输入 + 本文件独立声明的预期。

    预期不从页面或流程定义里取：从被测物里取预期等于让模型自证。variants 至少两组且换
    运行时输入——只跑一组，「写死的 selector 加恰好对上的数据」和「真的按条件筛」分不开。

    预期是整行业务字段，不是主键列表：单号对得上只证明筛出了哪几条记录，证明不了这几条
    记录的日期和金额是对的——日期填错框、金额抓错列都会留下一份主键完全正确的结果。

    日期一律按本案例声明的 date_formats 解析后逐项比年月日。formats 只收年在最前的写法，
    解析不出来就报「无法验证」：按数字组猜日月顺序会把 03/02 与 02/03 判成同一天。
    """

    name: str
    page: str
    kind: str  # table | scalar
    requirement: str
    variants: list[dict[str, str]]
    date_formats: tuple[str, ...]
    id_key: str = ""
    output_variable: str = ""
    row_fields: tuple[str, ...] = ()
    expected_records: list[list[list[str]]] = field(default_factory=list)
    date_field: str = ""
    numeric_fields: tuple[str, ...] = ()
    readback_expect: tuple[tuple[str, str], ...] = ()
    note: str = ""


CASES: list[E2ECase] = [
    E2ECase(
        name="filter_enter_commit",
        page="filter_enter_commit.html",
        kind="table",
        requirement=(
            "把页面上的开始日期、结束日期筛成 start_date 到 end_date 这个区间并提交筛选，"
            "再把筛选后的表格抓成变量 rows（每行含 单号、日期、金额）。"
            "两个日期必须是 input_variables，运行时由 variables 传入，不要写死在节点里。"
        ),
        output_variable="rows",
        id_key="单号",
        row_fields=("单号", "日期", "金额"),
        date_field="日期",
        numeric_fields=("金额",),
        date_formats=("%Y-%m-%d",),
        variants=[
            {"start_date": "2026-06-01", "end_date": "2026-06-30"},
            {"start_date": "2026-05-01", "end_date": "2026-06-05"},
        ],
        expected_records=[
            [["B-02", "2026-06-02", "200"], ["B-03", "2026-06-15", "300"]],
            [["B-01", "2026-05-28", "100"], ["B-02", "2026-06-02", "200"]],
        ],
        note="只在 keydown Enter 上提交：写完文本不按键，回读值正常但筛选没生效。",
    ),
    E2ECase(
        name="filter_change_commit",
        page="filter_change_commit.html",
        kind="table",
        requirement=(
            "把「记账日期」的起止筛成 start_date 到 end_date 并提交筛选，"
            "再把筛选后的表格抓成变量 rows（每行含 票据号、记账日期、金额）。"
            "页面上还有一个与筛选无关的日期输入框，不要动它。"
            "两个日期必须是 input_variables，运行时由 variables 传入。"
        ),
        output_variable="rows",
        id_key="票据号",
        row_fields=("票据号", "记账日期", "金额"),
        date_field="记账日期",
        numeric_fields=("金额",),
        date_formats=("%Y/%m/%d",),
        variants=[
            {"start_date": "2026/06/01", "end_date": "2026/06/30"},
            {"start_date": "2026/05/01", "end_date": "2026/06/05"},
        ],
        expected_records=[
            [["V-02", "2026/06/02", "200"], ["V-03", "2026/06/15", "300"]],
            [["V-01", "2026/05/28", "100"], ["V-02", "2026/06/02", "200"]],
        ],
        note="change（失焦）提交 + 另有无关日期框：填错框时页面不报错，只是不筛。",
    ),
    E2ECase(
        name="pagination_sweep",
        page="pagination.html",
        kind="table",
        requirement=(
            "把这张分页表格的所有行汇总成变量 rows，一行都不能漏。"
            "翻页上限用 input_variable max_pages 控制，运行时由 variables 传入，"
            "不要把翻几页写死在节点里。"
        ),
        output_variable="rows",
        row_fields=("单号",),
        date_formats=(),
        variants=[{"max_pages": "20"}, {"max_pages": "2"}],
        # 第二组量「上限真的由输入变量决定」：翻页次数写死的流程在第一组也全对。
        expected_records=[
            [["P1-A"], ["P2-A"], ["P3-A"]],
            [["P1-A"], ["P2-A"]],
        ],
        note="翻页只换一行内容，且末页才置灰；停在第 1 页是这一类页面的已知失败模式。",
    ),
    E2ECase(
        name="holdout_custom_daterange",
        page="holdout/custom_daterange.html",
        kind="scalar",
        requirement=(
            "把页面上的日期区间填成 start_date 到 end_date，"
            "再把两个日期框各自的当前值回读成变量 start_readback 与 end_readback。"
            "两个日期必须是 input_variables，运行时由 variables 传入。"
        ),
        variants=[
            {"start_date": "2026-06-01", "end_date": "2026-06-30"},
            {"start_date": "2025-03-02", "end_date": "2025-03-20"},
        ],
        readback_expect=(("start_readback", "start_date"), ("end_readback", "end_date")),
        # 留出页的组件可能改写显示格式，所以这里列出允许的写法而不是断定一种；但列表只收年在
        # 最前的写法：一旦允许 %d/%m/%Y，03/02 与 02/03 就没法区分，日月填反会判成通过。
        date_formats=("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y.%m.%d"),
        note="留出页：没有数据表，只能用回读值当交付物；第二组输入的日月都小于 13，日月填反会被这一组抓到。",
    ),
]


def _parse_date(text: str, formats: Sequence[str]) -> date | None:
    """按案例声明的格式解析日期；一个都不匹配就返回 None，由调用方报「无法验证」。

    只认 formats 里的写法，不做「看着像日期就拆数字」的兜底：拆数字必须假设日月顺序，
    而 03/02 与 02/03 在两种顺序下互为对方，猜错的那一半会把日月填反判成通过。
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _row_values(row: Any, row_fields: Sequence[str]) -> tuple[list[str], list[str]]:
    """把一行摊成与 row_fields 对齐的字符串列表，并单独交出缺掉的字段名。

    缺列必须报成缺列，不能靠位置补齐后当成值错：抓漏一整列（少抓金额）和抓错值（金额抓成
    另一列）要给出不同的失败原因，否则修的人会去改 selector 而不是补字段。
    单列表格按 list[list] 交出，多列按 dict（见 _TABLE_EXTRACT_SCRIPT）。
    """
    if isinstance(row, dict):
        values = [str(row.get(field, "")).strip() for field in row_fields]
        missing = [field for field in row_fields if field not in row]
        return values, missing
    cells = list(row) if isinstance(row, list) else [row]
    values = [str(cells[i]).strip() if i < len(cells) else "" for i in range(len(row_fields))]
    missing = [field for i, field in enumerate(row_fields) if i >= len(cells)]
    return values, missing


async def _task_variables(task_manager: Any, task_id: str) -> dict[str, Any]:
    """运行结束后的变量表，取法与 ai_checks.audit_run 一致。

    判据只从这里取数：读流程定义是「模型说它抓了什么」，读页面是「页面说它有什么」。
    """
    snapshot = await task_manager.get_task(task_id)
    if snapshot is None:
        return {}
    return {snap.name: _parse_runtime_value(snap.value) for snap in (snapshot.variables or [])}


def _judge_readback(
    case: E2ECase, index: int, variables: dict[str, Any], detail: dict[str, Any]
) -> list[str]:
    """核对回读交付物：每个回读值解析成日期后与它对应的那个输入逐项比年月日，再比起止顺序。

    比年份是放行月日错误：2026-06-30 和 2026-06-01 年份一样，日期填错框照样过。解析不出来
    只能报「无法验证」——降级成年份比较等于把最容易出错的那两位放掉。
    """
    failures: list[str] = []
    variant = case.variants[index]
    values = {name: str(variables.get(name) or "").strip() for name, _ in case.readback_expect}
    detail["readback"] = values
    parsed: dict[str, date | None] = {}
    for name, input_key in case.readback_expect:
        raw = values[name]
        want = _parse_date(str(variant.get(input_key) or ""), case.date_formats)
        if want is None:
            failures.append(
                f"案例输入 {input_key}={variant.get(input_key)!r} 不符合 date_formats="
                f"{case.date_formats}，判据自身立不起期望"
            )
        if not raw:
            failures.append(f"{name} 是空的：没有回读到值")
            parsed[name] = None
            continue
        got = _parse_date(raw, case.date_formats)
        parsed[name] = got
        if got is None:
            failures.append(
                f"{name}={raw!r} 不符合本案例声明的任一格式 {case.date_formats}，无法验证是哪一天"
            )
        elif want is not None and got != want:
            failures.append(
                f"{name} 应为 {want.isoformat()}（输入 {input_key}），实际回读 {got.isoformat()}"
            )
    ordered = [parsed.get(name) for name, _ in case.readback_expect]
    if len(ordered) == 2 and all(ordered):
        start, end = ordered[0], ordered[1]
        if start > end:  # type: ignore[operator]
            failures.append(f"起止顺序颠倒：start={start} 晚于 end={end}")
        elif start == end:
            failures.append(f"起止回读到同一天，说明两个日期填进了同一个框：{values}")
    return failures


def _judge_table(
    case: E2ECase, index: int, variables: dict[str, Any], detail: dict[str, Any]
) -> list[str]:
    """核对表格交付物：字段齐不齐、记录集合对不对、有没有重复、每个字段的值对不对。

    主键对得上只证明筛出了哪几条记录，证明不了这几条的日期和金额是对的——日期填错框、
    金额抓错列都会留下一份主键完全正确的结果，所以集合比完还要逐字段比。
    """
    failures: list[str] = []
    expected = case.expected_records[index]
    detail["expected"] = expected
    rows = variables.get(case.output_variable)
    if not isinstance(rows, list):
        detail["missing"] = [row[0] for row in expected]
        return [f"没有产出需求点名的变量 {case.output_variable}（拿到 {type(rows).__name__}）"]

    got: list[list[str]] = []
    absent_fields: list[str] = []
    unnamed_multicolumn_rows = False
    for row in rows:
        if len(case.row_fields) > 1 and not isinstance(row, dict):
            unnamed_multicolumn_rows = True
        values, absent = _row_values(row, case.row_fields)
        got.append(values)
        absent_fields.extend(absent)
    detail["got"] = got
    if unnamed_multicolumn_rows:
        failures.append("多列表格没有字段名，无法确认各列对应的业务字段")
    if absent_fields:
        failures.append("这些字段没抓到：" + "、".join(dict.fromkeys(absent_fields)))

    id_pos = case.row_fields.index(case.id_key) if case.id_key in case.row_fields else 0
    expected_ids = [row[id_pos] for row in expected]
    got_ids = [row[id_pos] if id_pos < len(row) else "" for row in got]
    detail["missing"] = [rid for rid in expected_ids if rid not in got_ids]
    detail["extra"] = [rid for rid in got_ids if rid not in expected_ids]
    if detail["missing"]:
        failures.append(f"少了这些记录：{detail['missing']}")
    if detail["extra"]:
        failures.append(f"多了这些记录：{detail['extra']}")
    duplicated = [rid for rid in dict.fromkeys(got_ids) if got_ids.count(rid) > 1]
    if duplicated:
        # 翻页时重复抓同一页会让行数「够了」，只比集合看不出来
        failures.append(f"这些记录重复出现：{duplicated}")

    got_by_id: dict[str, list[list[str]]] = {}
    for row, rid in zip(got, got_ids, strict=True):
        got_by_id.setdefault(rid, []).append(row)
    for expected_row in expected:
        for candidate in got_by_id.get(expected_row[id_pos], []):
            failures.extend(_judge_row(case, expected_row, candidate))
    if not detail["missing"] and not detail["extra"] and not duplicated and got_ids != expected_ids:
        failures.append(f"记录顺序与页面不一致：期望 {expected_ids}，实际 {got_ids}")
    return failures


def _judge_row(case: E2ECase, expected_row: list[str], got_row: list[str]) -> list[str]:
    """逐字段核对一条记录。日期按格式解析后比年月日，金额先要是个数，其余按文本比。

    日期不按文本比：同一天在页面上可能写成 2026-06-02 或 2026/06/02，文本不等不代表日子不对。
    金额单独判类型：抓错列常常抓回一串非数字，「不是数字」和「数字不对」要分开说。
    """
    failures: list[str] = []
    row_id = expected_row[0] if expected_row else ""
    for field_name, want, have in zip(case.row_fields, expected_row, got_row, strict=False):
        if field_name == case.date_field:
            want_date = _parse_date(want, case.date_formats)
            have_date = _parse_date(have, case.date_formats)
            if want_date is None:
                failures.append(f"案例自己声明的期望日期 {want!r} 不符合 date_formats={case.date_formats}")
            elif have_date is None:
                failures.append(
                    f"{row_id} 的 {field_name}={have!r} 不符合本案例声明的日期格式 "
                    f"{case.date_formats}，无法验证是哪一天"
                )
            elif have_date != want_date:
                failures.append(
                    f"{row_id} 的 {field_name} 应为 {want_date.isoformat()}，实际 {have_date.isoformat()}"
                )
            continue
        if field_name in case.numeric_fields:
            try:
                have_num = float(have.replace(",", ""))
            except ValueError:
                failures.append(f"{row_id} 的 {field_name}={have!r} 不是数字")
                continue
            if not math.isfinite(have_num):
                failures.append(f"{row_id} 的 {field_name}={have!r} 不是有限数字")
                continue
            if abs(have_num - float(want.replace(",", ""))) > 1e-9:
                failures.append(f"{row_id} 的 {field_name} 应为 {want}，实际 {have}")
            continue
        if have != want:
            failures.append(f"{row_id} 的 {field_name} 应为 {want!r}，实际 {have!r}")
    return failures


def _judge_variant(
    case: E2ECase, index: int, run: dict[str, Any], variables: dict[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    """判一次运行。返回 (失败原因, 明细)；失败原因为空 = 这一组输入通过。"""
    failures: list[str] = []
    detail: dict[str, Any] = {"input": case.variants[index], "run_status": str(run.get("status") or "")}
    if detail["run_status"] != "success":
        # 没跑成功就没有数据可判，平台给的状态原样带进报告，不翻译成「失败」
        failures.append(
            f"运行未成功：status={detail['run_status']} "
            f"{run.get('error_summary') or run.get('message') or ''}".strip()
        )
        return failures, detail

    audit = run.get("acceptance_audit") or {}
    if audit.get("passed") is False:
        # 流程自己冻结的验收契约没过：这条流程交不出它承诺的产物，不能算通过
        failures.append(f"验收契约未通过：{audit.get('issues') or audit.get('message')}")
    detail["acceptance_passed"] = audit.get("passed")

    if case.kind == "table":
        failures.extend(_judge_table(case, index, variables, detail))
    else:
        failures.extend(_judge_readback(case, index, variables, detail))
    return failures, detail


class RecordingExecutor:
    """真实 RpaToolExecutor 的记录代理：只记调用，不改行为。

    模型发起的调用与平台重建状态块的读取分开记：状态块每轮都 get_flow/lint_flow，混进
    calls 会让「模型调了几次工具」和成本统计失真。
    签名必须与 RpaToolExecutor.execute 逐字一致（含 progress_sink / change_context）：
    不一致时编排层把 TypeError 当成「工具执行失败」吞掉，看起来像模型能力问题。
    """

    def __init__(self, inner: RpaToolExecutor) -> None:
        self._inner = inner
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.platform_calls: list[tuple[str, dict[str, Any]]] = []
        self.created_flow_ids: list[str] = []
        self.evidence: list[dict[str, Any]] = []
        self.recording = False

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        progress_sink: dict[str, Any] | None = None,
        change_context: Any = None,
    ) -> dict[str, Any]:
        record = None
        if self.recording:
            bucket = self.calls if name in _MODEL_FACING_TOOLS else self.platform_calls
            bucket.append((name, deepcopy(args)))
            if name in _MODEL_FACING_TOOLS:
                record = {"name": name, "flow_id": str(args.get("flow_id") or ""), "outcome": "pending"}
                self.evidence.append(record)
        try:
            result = await self._inner.execute(name, args, progress_sink, change_context)
        except BaseException as error:
            if record is not None:
                record.update(outcome="exception", exception={
                    "type": type(error).__name__, "message": _redact(str(error)),
                })
            raise
        if record is not None:
            # 页面 DOM、截图和任意工具参数不进入报告；验收只保留运行身份与裁决。
            summary = {
                key: deepcopy(result[key]) for key in ("status", "flow_id", "task_id", "error", "issues", "expected_parameters")
                if key in result
            }
            audit = result.get("acceptance_audit")
            if isinstance(audit, dict):
                summary["acceptance_audit"] = {"passed": audit.get("passed")}
            # blocking_lint_findings 是「流程存在但跑不起来」的唯一说明，只留 status 等于
            # 报告说了「被拦」却不说拦在哪，判节点配置/连线只能回头翻隔离库重算。
            # issue/severity/node_id 都是判据自己产出的枚举，不含页面数据。
            findings = result.get("lint_findings")
            if isinstance(findings, list):
                summary["lint_findings"] = [
                    {k: f.get(k) for k in ("severity", "issue", "node_id")}
                    for f in findings if isinstance(f, dict)
                ]
            record.update(outcome="returned", result={
                key: _redact(value) if isinstance(value, str) else value
                for key, value in summary.items()
            })
            if name == "create_flow" and result.get("flow_id") and _record_succeeded(record):
                self.created_flow_ids.append(str(result["flow_id"]))
        return result

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)

    def reset(self) -> None:
        self.calls.clear()
        self.platform_calls.clear()
        self.created_flow_ids.clear()
        self.evidence.clear()

    def saved_flow_id(self) -> str:
        """从模型调过的写入类工具里取：取库里「最后一条流程」会在有脏数据时判到别人的流程上。"""
        for record in reversed(self.evidence):
            if not _record_succeeded(record):
                continue
            if record["name"] in ("run_flow", "publish_flow", "set_acceptance_contract", "update_flow"):
                flow_id = record["flow_id"]
                if flow_id:
                    return flow_id
        return self.created_flow_ids[-1] if self.created_flow_ids else ""


def _record_succeeded(record: dict[str, Any]) -> bool:
    result = record.get("result", {})
    if record["outcome"] != "returned" or result.get("error"):
        return False
    status = result.get("status")
    if record["name"] == "run_flow":
        return status == "success"
    return status in (*get_args(FlowStatus), "applied", "success") or (
        status is None and bool(result.get("flow_id"))
    )


def _redact(text: str) -> str:
    return re.sub(r"(?i)Bearer\s+\S+|\b(?:rc|sk)-[a-zA-Z0-9_-]+", "[REDACTED]", text)


_SYSTEM_HINT = (
    "这是本地测试页面，可以放心观察和操作。请先看页面，再建流程，"
    "然后用 run_flow 实际跑一次确认结果，不要只写流程不运行。"
)


def _model_messages(case: E2ECase, url: str) -> list[dict[str, Any]]:
    prompt = (
        f"页面地址：{url}\n\n{case.requirement}\n\n{_SYSTEM_HINT}\n"
        f"首次运行使用 variables：{json.dumps(case.variants[0], ensure_ascii=False)}"
    )
    messages = [{"role": "user", "content": prompt}]
    intents = _detect_turn_intents(messages, None, FlowState(flow_id=None))
    if intents.create_url != url:
        raise ValueError(f"评测 URL 未被创建意图识别：{url}")
    if not _tool_schemas_for_round(GuardState(), intents):
        raise ValueError("评测首轮工具集合为空")
    return messages


async def _drive_model(
    orchestrator: AiOrchestrator, recorder: RecordingExecutor, model: str, case: E2ECase, url: str
) -> dict[str, Any]:
    """让模型自己看页面、建流程、跑一次。只收事件，不替它做任何决定。"""
    messages = _model_messages(case, url)
    texts: list[str] = []
    usage: dict[str, Any] | None = None
    errors: list[str] = []
    recorder.reset()
    recorder.recording = True
    started = time.monotonic()
    try:
        async for event in orchestrator.stream(
            messages=messages, model=model, flow_id=None
        ):
            kind = event.get("type")
            if kind == "text":
                texts.append(str(event.get("delta") or event.get("text") or ""))
            elif kind == "usage":
                usage = event.get("usage") or event
            elif kind == "error":
                errors.append(_redact(str(event.get("message") or event)))
    except Exception as error:
        errors.append(_redact(f"{type(error).__name__}: {error}"))
    finally:
        recorder.recording = False
    return {
        "seconds": round(time.monotonic() - started, 1),
        "reply": "".join(texts)[-2000:],
        "usage": usage,
        "errors": errors,
    }


async def _replay_variants(
    executor: RpaToolExecutor, case: E2ECase, flow_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """换一组输入数据重放同一个流程：每个变体各跑一次真实执行，逐个判分。"""
    runs: list[dict[str, Any]] = []
    verdicts: list[dict[str, Any]] = []
    for index, variant in enumerate(case.variants):
        run = await executor.execute("run_flow", {"flow_id": flow_id, "variables": dict(variant)})
        variables = await _task_variables(executor._task_manager, str(run.get("task_id") or ""))
        failures, detail = _judge_variant(case, index, run, variables)
        runs.append(run)
        verdicts.append({"variant": variant, "passed": not failures, "failures": failures, **detail})
    return runs, verdicts


_READBACK_GATE = """
if _vars["start_readback"] != _vars["start_date"] or _vars["end_readback"] != _vars["end_date"]:
    raise SystemExit(f"回读不符: {_vars['start_readback']!r} {_vars['end_readback']!r}")
"""

_ROW_GATE = """
rows = _vars["rows"]
bad = [r for r in rows if not (_vars["start_date"] <= str(r["日期"]) <= _vars["end_date"])]
if bad:
    raise SystemExit(f"筛选未生效，越界行: {bad}")
if not rows:
    raise SystemExit("筛选后一行都没有，无法证明筛选生效")
"""


def _self_check_flow(case: E2ECase, base_url: str) -> dict[str, Any]:
    """手写流程的 create_flow 参数：选择器写死，因为自检只验接线，不验模型识别。

    走 executor.execute 而不是直接调 flow_service：要验的正是工具分派 → 执行器 → 存储
    → run_flow 闸门 → 任务变量这一整条线。
    """
    return {
        "name": "e2e 自检：日期筛选取数",
        "description": "run_e2e --self-check 用的手写流程，只验执行器接线",
        "input_variables": [
            {"name": "start_date", "type": "String", "value": case.variants[0]["start_date"]},
            {"name": "end_date", "type": "String", "value": case.variants[0]["end_date"]},
        ],
        "nodes": [
            {"id": "n1", "type": "browser.open", "targetUrl": page_url(case.page, base_url)},
            {"id": "n2", "type": "browser.fill", "selector": "#q-start",
             "inputValue": "${var.start_date}", "delayMs": 200},
            {"id": "n3", "type": "browser.fill", "selector": "#q-end",
             "inputValue": "${var.end_date}", "delayMs": 200},
            {"id": "n4", "type": "browser.press", "selector": "#q-end",
             "inputValue": "Enter", "delayMs": 300},
            # outputVariable 是 lint 的 error 级要求（extract_no_output）：只写
            # firstValueVariable 会被 run_flow 的 blocking_lint_findings 挡在启动之前
            {"id": "n5", "type": "browser.extract", "selector": "#q-start",
             "extractMode": "attribute", "attribute": "value",
             "outputVariable": "start_readback_all", "firstValueVariable": "start_readback"},
            {"id": "n6", "type": "browser.extract", "selector": "#q-end",
             "extractMode": "attribute", "attribute": "value",
             "outputVariable": "end_readback_all", "firstValueVariable": "end_readback"},
            {"id": "n7", "type": "script.python", "code": _READBACK_GATE,
             "inputVariables": ["start_date", "end_date", "start_readback", "end_readback"]},
            {"id": "n8", "type": "browser.extract", "selector": "#bill-body tr",
             "extractMode": "table", "outputVariable": "rows", "countVariable": "rows_count"},
            {"id": "n9", "type": "script.python", "code": _ROW_GATE,
             "inputVariables": ["rows", "start_date", "end_date"]},
        ],
        "edges": [
            {"source": f"n{i}", "target": f"n{i + 1}"} for i in range(1, 9)
        ],
        "acceptance_contract": {
            "requirements": [
                {"id": "r1", "description": "按日期区间筛选账单并取出筛选后的表格",
                 "source_kind": "user", "source_quote": case.requirement[:80],
                 "confidence": 0.95, "confirmed": True}
            ],
            "deliverables": [
                {"id": "d1", "variable": "rows", "kind": "table", "required": True,
                 "min_rows": 1, "required_fields": ["单号", "日期"], "unique_by": ["单号"],
                 "requirement_ids": ["r1"]}
            ],
        },
    }


def _verdict(case: E2ECase, stage: str, verdicts: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    passed = bool(verdicts) and all(v["passed"] for v in verdicts)
    variants_total = len(case.variants)
    return {
        "case": case.name,
        "page": case.page,
        "stage": stage,
        "passed": passed,
        "variants_passed": sum(1 for v in verdicts if v["passed"]),
        "variants_total": variants_total,
        "verdicts": verdicts,
        **extra,
    }


async def run_self_check(executor: RpaToolExecutor, case: E2ECase) -> dict[str, Any]:
    """手写流程走真实执行器：证明接线与判分今天就能跑，与模型能力无关。"""
    with fixture_server() as base_url:
        return await _run_self_check(executor, case, base_url)


async def _run_self_check(executor: RpaToolExecutor, case: E2ECase, base_url: str) -> dict[str, Any]:
    created = await executor.execute("create_flow", _self_check_flow(case, base_url))
    flow_id = str(created.get("flow_id") or "")
    if not flow_id:
        return _verdict(case, "create_flow", [], error=created)
    runs, verdicts = await _replay_variants(executor, case, flow_id)
    return _verdict(case, "done", verdicts, flow_id=flow_id, statuses=[r.get("status") for r in runs])


async def run_model_case(
    orchestrator: AiOrchestrator,
    recorder: RecordingExecutor,
    model: str,
    case: E2ECase,
) -> dict[str, Any]:
    """模型自己看页面、建流程、跑一次，然后换输入数据重放并判分。"""
    with fixture_server() as base_url:
        return await _run_model_case(orchestrator, recorder, model, case, base_url)


async def _run_model_case(
    orchestrator: AiOrchestrator, recorder: RecordingExecutor, model: str, case: E2ECase, base_url: str,
) -> dict[str, Any]:
    with _observe_guards() as guard_hits:
        turn = await _drive_model(orchestrator, recorder, model, case, page_url(case.page, base_url))
    flow_id = recorder.saved_flow_id()
    metrics = collect_run_metrics(recorder.calls, turn["usage"], guard_hits)
    common = {
        "model_seconds": turn["seconds"],
        "model_errors": turn["errors"],
        "model_tool_calls": [name for name, _ in recorder.calls],
        "metrics": asdict(metrics),
        "model_tool_evidence": deepcopy(recorder.evidence),
        "reply_tail": turn["reply"][-400:],
    }
    # 上游一个回合都没给（配额用尽、真限流、中转根本没有这个模型）时这个案例没跑过：
    # 没有流程、没有运行、没有可判的数据。计成 FAIL 会让「上游挂了」冒充「模型没通过」，
    # 分母里还多一个从未执行的案例，通过率跟着失真。
    # 判据只看有没有回合，不解析错误文本：中转把「没有这个模型」也写成 rate-limited,
    # 按关键词分流会把两种处置（等配额 / 永远等不到）判反。
    if metrics.rounds == 0 and turn["errors"]:
        return _verdict(case, "model_unreachable", [], not_run=True, replay_passed=False,
                        model_execution={"passed": False, "failures": ["上游未返回任何回合"]}, **common)
    if not flow_id:
        return _verdict(case, "no_flow_saved", [], replay_passed=False,
                        model_execution={"passed": False, "failures": ["模型未保存流程"]}, **common)
    model_execution = await _judge_model_execution(recorder, case, flow_id)
    runs, verdicts = await _replay_variants(recorder._inner, case, flow_id)
    replay_passed = bool(verdicts) and all(v["passed"] for v in verdicts)
    return _verdict(
        case, "done", verdicts, flow_id=flow_id,
        statuses=[r.get("status") for r in runs], replay_passed=replay_passed,
        model_execution=model_execution,
        passed=replay_passed and model_execution["passed"] and not turn["errors"], **common,
    )


async def _judge_model_execution(
    recorder: RecordingExecutor, case: E2ECase, flow_id: str,
) -> dict[str, Any]:
    mutations = {"create_flow", "update_flow", "apply_node_fix", "set_acceptance_contract"}
    for record in reversed(recorder.evidence):
        result = record.get("result", {})
        target = record["flow_id"] or result.get("flow_id")
        if target != flow_id:
            continue
        if record["name"] in mutations and _record_succeeded(record):
            return {"passed": False, "failures": ["模型最后修改流程后未取得新的运行验收证据"]}
        if record["name"] != "run_flow":
            continue
        task_id = str(result.get("task_id") or "")
        if (record["outcome"] != "returned" or result.get("status") != "success"
                or not task_id or result.get("acceptance_audit", {}).get("passed") is not True):
            return {"passed": False, "task_id": task_id,
                    "failures": ["模型最后一次运行缺少成功任务与通过的验收证据"]}
        variables = await _task_variables(recorder._task_manager, task_id)
        failures, detail = _judge_variant(case, 0, result, variables)
        return {"passed": not failures, "task_id": task_id, "failures": failures, **detail}
    return {"passed": False, "failures": ["模型未实际运行保存的流程"]}


def _data_score(results: list[dict[str, Any]]) -> dict[str, Any]:
    """数据正确性与完整性分开报：全对、少行、多行是三种不同的失败，处置也不同。"""
    variants = [v for r in results for v in r.get("verdicts") or []]
    missing = sum(1 for v in variants if v.get("missing"))
    extra = sum(1 for v in variants if v.get("extra"))
    return {
        "variants_total": len(variants),
        "variants_passed": sum(1 for v in variants if v.get("passed")),
        "variants_missing_rows": missing,
        "variants_extra_rows": extra,
    }


def build_report(
    *, model: str, ran_online: bool, results: list[dict[str, Any]],
    self_check: list[dict[str, Any]], env: dict[str, str], not_run_reason: str = "",
    served_by: str = "",
) -> dict[str, Any]:
    # 上游没给回合的案例不进分母：它没跑过，既不算通过也不算失败。留在分母里
    # 会把「4 个案例过了 2 个」这种读数变成对模型的指控，而其中一个从未执行。
    ran = [r for r in results if not r.get("not_run")]
    return {
        "model": model,
        # 中转没有目标模型时 _resolve_relay_model 会按 family/名称模糊匹配到另一个，
        # 报告只写请求的 model 等于把结果挂到评测时根本没跑的模型头上。
        "served_by": served_by,
        "model_substituted": bool(served_by) and served_by != model.split("/", 1)[-1],
        "online_ran": ran_online,
        "not_run_reason": not_run_reason,
        # 成功率只统计模型自己生成的流程；--self-check 是手写流程，单独一栏
        "model_e2e": {
            "cases_total": len(ran),
            "cases_passed": sum(1 for r in ran if r["passed"]),
            "cases_not_run": len(results) - len(ran),
            "replay_cases_passed": sum(1 for r in ran if r.get("replay_passed")),
            "model_execution_cases_passed": sum(1 for r in ran if r.get("model_execution", {}).get("passed")),
            **_data_score(ran),
        },
        "self_check": {
            "cases_total": len(self_check),
            "cases_passed": sum(1 for r in self_check if r["passed"]),
            **_data_score(self_check),
        },
        "results": results,
        "self_check_results": self_check,
        "isolation": {key: value for key, value in env.items()},
    }


def print_report(report: dict[str, Any]) -> None:
    if report.get("model_substituted"):
        print(f"\n模型被中转替换：请求 {report['model']}，实际服务 {report['served_by']}")
    for title, key in (("模型端到端", "results"), ("自检（手写流程，不计入模型成功率）", "self_check_results")):
        rows = report.get(key) or []
        if not rows:
            continue
        print(f"\n{title}")
        for row in rows:
            mark = "NOT-RUN" if row.get("not_run") else "PASS" if row["passed"] else "FAIL"
            print(f"  [{mark}] {row['case']}  变体 {row['variants_passed']}/{row['variants_total']}  阶段={row['stage']}")
            # 上游错误原样打出来：litellm 只在 stderr 留「Give Feedback / Get Help」，
            # 真正的原因（配额用尽 / 中转没有这个模型）此前只存在 report.json 里，
            # 看命令输出的人拿到的是一排 FAIL，方向会判到模型能力上。
            for message in row.get("model_errors") or []:
                print(f"         ! 上游：{message}")
            for verdict in row.get("verdicts") or []:
                for failure in verdict.get("failures") or []:
                    print(f"         - {failure}")
            if "replay_passed" in row and not row.get("not_run"):
                print(f"         外部重放={row['replay_passed']} 模型自行验收={row['model_execution']['passed']}")
                for failure in row["model_execution"].get("failures", []):
                    print(f"         - {failure}")
            if row.get("error"):
                print(f"         - {row['error']}")
            metrics = row.get("metrics") or {}
            if metrics:
                print(
                    f"         轮次={metrics.get('rounds')} 工具调用={metrics.get('tool_calls')}"
                    f" 重复={metrics.get('duplicate_calls')} 被拦={metrics.get('blocked_calls')}"
                    f" tokens={metrics.get('prompt_tokens')}+{metrics.get('completion_tokens')}"
                    f"（cached {metrics.get('cached_tokens')}）"
                )
    summary = report["model_e2e"]
    print(f"\n模型端到端：案例 {summary['cases_passed']}/{summary['cases_total']}，"
          f"变体 {summary['variants_passed']}/{summary['variants_total']}，"
          f"缺行 {summary['variants_missing_rows']}，多行 {summary['variants_extra_rows']}")
    if summary.get("cases_not_run"):
        print(f"其中 {summary['cases_not_run']} 个案例未运行（上游未返回任何回合），已排除在分母外")
    if not report["online_ran"]:
        print(f"⚠️  真实模型端到端【未运行】：{report['not_run_reason']}（exit 0，不代表通过）")


async def _with_runtime(root: Path, body: Any) -> Any:
    """隔离环境下起一套真实运行时服务，跑完必关。

    必须在 apply_isolation 之后构造：TaskManager 在 __init__ 里就把浏览器 session 目录
    固定下来了，先建后隔离等于拿用户真实 profile 去跑评测。
    """
    settings = load_settings()
    services = create_runtime_services(settings, LogBroker())
    await services.start()
    executor = RpaToolExecutor(
        flow_service=services.flow_service,
        task_manager=services.task_manager,
        schedule_service=services.schedule_service,
    )
    try:
        return await body(executor)
    finally:
        await services.close()


def _select(names: list[str]) -> list[E2ECase]:
    if not names:
        return list(CASES)
    wanted = {n.strip() for part in names for n in part.split(",") if n.strip()}
    unknown = wanted - {case.name for case in CASES}
    if unknown:
        raise SystemExit(f"未知案例 {sorted(unknown)}；可选：{[c.name for c in CASES]}")
    return [case for case in CASES if case.name in wanted]


async def _amain(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve() if args.root else Path(tempfile.mkdtemp(prefix="rpa-e2e-"))
    prepare_root(root)
    # 自检流程的选择器是按 filter_enter_commit.html 手写的，换案例不成立；
    # 静默忽略 --only 会让人以为自检覆盖了留出页
    if args.self_check and args.only:
        raise SystemExit(f"--self-check 只跑手写案例 {CASES[0].name}，不接受 --only")
    cases = _select(args.only)

    # 必须在 apply_isolation 之前构造，它才读得到用户真实 ai/config.json；
    # database_url 显式给隔离后的那个，模型目录表不会写进用户库。
    isolated = isolation_env(root)
    config = AiConfigService(database_url=isolated["DATABASE_URL"])
    model, has_key = _resolve_model_and_key(config, args.model)
    if has_key:
        config.apply_to_env(config.load())  # 只进 os.environ 供 litellm 读，不落盘、不打印

    env = apply_isolation(root)
    print(f"隔离根目录：{root}")

    # 只读地算一遍中转实际会派给哪个模型：报告里必须带上，否则「qwen3.7-max 评测结果」
    # 可能是 Qwen3.8-Flash-Next 答的。取不到（无 base_url / 中转不通）就留空，不猜。
    served_by = ""
    if has_key:
        base_url = config.get_base_url_for_model(model)
        api_key = config.get_api_key_for_model(model)
        if base_url:
            from app.services.ai_orchestrator import _normalize_base_url, _resolve_relay_model
            try:
                resolved = await _resolve_relay_model(
                    model, _normalize_base_url(base_url), api_key or "sk-relay",
                )
                served_by = resolved.split("/", 1)[-1]
            except Exception:
                served_by = ""
    if served_by and served_by != model.split("/", 1)[-1]:
        print(f"注意：中转实际服务的模型是 {served_by}（请求的是 {model}）")

    async def _body(executor: RpaToolExecutor) -> dict[str, Any]:
        self_check: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        if args.self_check:
            self_check.append(await run_self_check(executor, CASES[0]))
        if not args.self_check and has_key:
            recorder = RecordingExecutor(executor)
            orchestrator = AiOrchestrator(recorder, config_service=config)
            for case in cases:
                results.append(await run_model_case(orchestrator, recorder, model, case))
        reason = "本次只跑 --self-check，没有调用模型" if args.self_check else "" if has_key else (
            f"模型 {model or '(未配置 default_model)'} 无可用 API Key 或 base_url："
            "请在应用内配置模型或设置对应环境变量后重跑同一条命令"
        )
        # 一个案例都没跑起来时整轮就是「未运行」，退出码不能是失败：否则上游限流
        # 会在 CI 上表现成模型退化，而重跑同一条命令是唯一的处置。
        ran = [r for r in results if not r.get("not_run")]
        if results and not ran:
            upstream = next((m for r in results for m in r.get("model_errors") or []), "")
            reason = f"上游未返回任何回合：{upstream}" if upstream else "上游未返回任何回合"
        return build_report(
            model=model, ran_online=bool(ran), results=results,
            self_check=self_check, env=env, not_run_reason=reason,
            served_by=served_by,
        )

    report = await _with_runtime(root, _body)
    path = root / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    print(f"\n报告：{path}")
    # 未运行不是失败，退出码保持 0；模型跑了但没过才算失败
    failed = [r for r in report["results"] + report["self_check_results"]
              if not r["passed"] and not r.get("not_run")]
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="真实模型 + 真实执行器端到端评测")
    parser.add_argument("--only", action="append", default=[], help="只跑指定案例，逗号分隔")
    parser.add_argument("--root", default="", help="隔离根目录，默认建临时目录")
    parser.add_argument("--model", default="", help="覆盖模型 id，默认用配置里的 default_model")
    parser.add_argument("--self-check", action="store_true",
                        help="不调模型：手写流程走同一条执行器链路，验证接线/隔离/判据")
    args = parser.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
