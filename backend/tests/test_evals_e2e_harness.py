"""端到端评测入口自身的确定性部分：隔离、判分、记录代理。

在线那一档平时跑不起来（要真实模型），不需要模型的部分必须全部钉住：判分器写错会给出
「模型能力没退化」的假绿灯，隔离写错会拿用户真实 profile 和真实库去跑评测——两者都不
报错，只会安静地把结论弄反。
"""

import asyncio
import inspect
import socket
from urllib.request import urlopen
from urllib.parse import urlsplit
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import config as app_config  # noqa: E402
from app.core import storage  # noqa: E402
from app.services.ai_tools.executor import RpaToolExecutor  # noqa: E402
from evals.run_e2e import (  # noqa: E402
    CASES,
    E2ECase,
    RecordingExecutor,
    _judge_variant,
    _judge_model_execution,
    _parse_date,
    _replay_variants,
    _row_values,
    isolation_env,
    fixture_server,
    page_url,
    _model_messages,
    run_model_case,
)


def test_isolation_env_moves_every_storage_path_under_the_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """所有落盘位置都必须在 root 下。漏一个就会写进用户的 ~/.easy-rpa：
    浏览器 profile 漏了会抢用户正在用的那个，数据库漏了会把评测流程混进用户流程列表。"""
    for key, value in isolation_env(tmp_path).items():
        monkeypatch.setenv(key, value)

    paths = [
        storage.resolve_app_data_dir(),
        storage.resolve_workspace_root(),
        storage.resolve_run_root(),
        storage.resolve_cache_dir(),
        storage.resolve_logs_dir(),
        storage.resolve_database_path(),
        storage.resolve_runtime_root(),
        storage.resolve_browser_profile_dir(),
        storage.resolve_browser_cookies_path(),
        storage.resolve_scrapling_storage_dir(),
        storage.resolve_ai_dir(),
        storage.resolve_ai_chats_dir(),
        storage.resolve_ai_config_path(),
    ]
    outside = [str(p) for p in paths if tmp_path not in Path(p).resolve().parents and Path(p).resolve() != tmp_path]
    assert not outside, outside
    # DATABASE_URL 也必须一起换：load_settings 优先读它，环境里残留一个就会连回用户库
    assert str(tmp_path) in app_config.load_settings().database_url


def test_isolation_env_does_not_touch_the_playwright_browser_cache(tmp_path: Path) -> None:
    """内核目录不进隔离表：跟着 root 走会让每次评测重新下载几百 MB 浏览器。"""
    assert "PLAYWRIGHT_BROWSERS_PATH" not in isolation_env(tmp_path)


def test_every_case_replays_with_at_least_two_different_inputs() -> None:
    """只跑一组输入，「写死的选择器 + 恰好对上的数据」和「真按条件筛」分不开。"""
    for case in CASES:
        assert len(case.variants) >= 2, case.name
        assert len({tuple(sorted(v.items())) for v in case.variants}) == len(case.variants), case.name
        if case.kind == "table":
            assert len(case.expected_records) == len(case.variants), case.name
            assert case.output_variable and case.row_fields, case.name
            # 预期是整行：只声明主键列的案例回到了「单号对上就算过」
            assert all(len(row) == len(case.row_fields) for rows in case.expected_records for row in rows), case.name
        else:
            assert case.readback_expect, case.name
        if case.date_field or case.readback_expect:
            assert case.date_formats, case.name


def test_declared_date_formats_never_put_the_day_before_the_month() -> None:
    """允许 %d/%m/%Y 就等于放掉日月填反：03/02 与 02/03 在两种顺序下互为对方。"""
    for case in CASES:
        for fmt in case.date_formats:
            assert fmt.index("%Y") == 0, (case.name, fmt)


def _table_case() -> E2ECase:
    return E2ECase(
        name="t", page="filter_enter_commit.html", kind="table", requirement="需求原文",
        variants=[{"start_date": "2026-06-01", "end_date": "2026-06-30"},
                  {"start_date": "2026-05-01", "end_date": "2026-06-05"}],
        id_key="单号", output_variable="rows",
        row_fields=("单号", "日期", "金额"), date_field="日期", numeric_fields=("金额",),
        date_formats=("%Y-%m-%d",),
        expected_records=[
            [["B-02", "2026-06-02", "200"], ["B-03", "2026-06-15", "300"]],
            [["B-01", "2026-05-28", "100"], ["B-02", "2026-06-02", "200"]],
        ],
    )


_V0_ROWS = [
    {"单号": "B-02", "日期": "2026-06-02", "金额": "200"},
    {"单号": "B-03", "日期": "2026-06-15", "金额": "300"},
]


def _scalar_case() -> E2ECase:
    return E2ECase(
        name="s", page="holdout/custom_daterange.html", kind="scalar", requirement="需求原文",
        variants=[{"start_date": "2026-06-01", "end_date": "2026-06-30"},
                  {"start_date": "2025-03-02", "end_date": "2025-03-20"}],
        readback_expect=(("start_readback", "start_date"), ("end_readback", "end_date")),
        date_formats=("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"),
    )


_OK_RUN = {"status": "success", "acceptance_audit": {"passed": True}}


def test_a_run_that_did_not_succeed_is_scored_by_its_own_status() -> None:
    """闸门拦下来的运行必须原样报出 status：翻成「数据不符」会让人去查选择器，
    而真正要看的是被哪个闸门拦了。"""
    failures, detail = _judge_variant(
        _table_case(), 0,
        {"status": "blocking_lint_findings", "message": "存在阻断级静态检查错误"},
        {"rows": _V0_ROWS},
    )
    assert len(failures) == 1
    assert "blocking_lint_findings" in failures[0] and "静态检查" in failures[0]
    assert detail["run_status"] == "blocking_lint_findings"


def test_matching_rows_pass_and_wrong_rows_report_missing_and_extra_separately() -> None:
    """缺行是筛过头或翻页停早了，多行是筛选没生效——处置完全不同，不能合成一句「数据不符」。"""
    case = _table_case()
    passed, _ = _judge_variant(case, 0, _OK_RUN, {"rows": _V0_ROWS})
    assert passed == []

    short, detail = _judge_variant(case, 0, _OK_RUN, {"rows": _V0_ROWS[:1]})
    assert short and detail["missing"] == ["B-03"] and detail["extra"] == []

    wide, detail = _judge_variant(
        case, 0, _OK_RUN,
        {"rows": [
            {"单号": "B-01", "日期": "2026-05-28", "金额": "100"},
            *_V0_ROWS,
            {"单号": "B-04", "日期": "2026-07-03", "金额": "400"},
        ]},
    )
    assert wide and detail["extra"] == ["B-01", "B-04"] and detail["missing"] == []


def test_a_repeated_page_is_caught_even_though_no_record_is_missing() -> None:
    """翻页重复抓同一页时行数「够了」、集合也全对，只比集合会放行。"""
    failures, detail = _judge_variant(
        _table_case(), 0, _OK_RUN, {"rows": [_V0_ROWS[0], *_V0_ROWS]},
    )
    assert detail["missing"] == [] and detail["extra"] == []
    assert any("重复" in f and "B-02" in f for f in failures), failures


def test_right_ids_with_a_wrong_month_a_wrong_day_or_a_wrong_amount_all_fail() -> None:
    """主键全对的三种坏结果：日期填错框（月错）、抓错行（日错）、抓错列（金额错）。"""
    case = _table_case()
    wrong_month, _ = _judge_variant(case, 0, _OK_RUN, {"rows": [
        {"单号": "B-02", "日期": "2026-07-02", "金额": "200"}, _V0_ROWS[1],
    ]})
    assert any("日期" in f and "2026-06-02" in f for f in wrong_month), wrong_month

    wrong_day, _ = _judge_variant(case, 0, _OK_RUN, {"rows": [
        {"单号": "B-02", "日期": "2026-06-20", "金额": "200"}, _V0_ROWS[1],
    ]})
    assert any("日期" in f and "2026-06-02" in f for f in wrong_day), wrong_day

    wrong_amount, _ = _judge_variant(case, 0, _OK_RUN, {"rows": [
        {"单号": "B-02", "日期": "2026-06-02", "金额": "-999"}, _V0_ROWS[1],
    ]})
    assert any("金额" in f and "200" in f for f in wrong_amount), wrong_amount


def test_a_missing_column_is_reported_as_a_missing_field_not_as_a_wrong_value() -> None:
    """少抓一整列要去补字段，抓错值要去改选择器：报成同一句话会把人引到错的修法。"""
    failures, _ = _judge_variant(_table_case(), 0, _OK_RUN, {"rows": [
        {"单号": "B-02", "日期": "2026-06-02"}, {"单号": "B-03", "日期": "2026-06-15"},
    ]})
    assert any("字段没抓到" in f and "金额" in f for f in failures), failures


def test_a_multicolumn_row_without_field_names_is_rejected() -> None:
    failures, _ = _judge_variant(_table_case(), 0, _OK_RUN, {"rows": [
        ["B-02", "2026-06-02", "200"], ["B-03", "2026-06-15", "300"],
    ]})
    assert any("字段名" in f for f in failures), failures


def test_an_unparseable_date_is_reported_as_unverifiable_not_quietly_passed() -> None:
    """格式不在案例声明里就只能说「无法验证」：降级成比年份等于放掉月日。"""
    failures, _ = _judge_variant(_table_case(), 0, _OK_RUN, {"rows": [
        {"单号": "B-02", "日期": "6/2/2026", "金额": "200"}, _V0_ROWS[1],
    ]})
    assert any("无法验证" in f for f in failures), failures

    not_a_number, _ = _judge_variant(_table_case(), 0, _OK_RUN, {"rows": [
        {"单号": "B-02", "日期": "2026-06-02", "金额": "二百"}, _V0_ROWS[1],
    ]})
    assert any("不是数字" in f for f in not_a_number), not_a_number

    not_finite, _ = _judge_variant(_table_case(), 0, _OK_RUN, {"rows": [
        {"单号": "B-02", "日期": "2026-06-02", "金额": "NaN"}, _V0_ROWS[1],
    ]})
    assert any("不是有限数字" in f for f in not_finite), not_finite


def test_the_same_day_written_in_another_declared_format_still_passes() -> None:
    """判据比的是哪一天，不是哪串文本：案例允许的写法之间不能互相判错。"""
    case = _table_case()
    case.date_formats = ("%Y-%m-%d", "%Y/%m/%d")
    failures, _ = _judge_variant(case, 0, _OK_RUN, {"rows": [
        {"单号": "B-02", "日期": "2026/06/02", "金额": "200"}, _V0_ROWS[1],
    ]})
    assert failures == []


def test_the_second_variant_is_judged_against_its_own_expectation() -> None:
    """换一组输入就必须换一份预期：两个变体共用一份预期时，写死日期的流程会全绿。"""
    case = _table_case()
    v1_rows = [
        {"单号": "B-01", "日期": "2026-05-28", "金额": "100"},
        {"单号": "B-02", "日期": "2026-06-02", "金额": "200"},
    ]
    assert _judge_variant(case, 1, _OK_RUN, {"rows": v1_rows})[0] == []
    stale, _ = _judge_variant(case, 1, _OK_RUN, {"rows": _V0_ROWS})
    assert stale, "第 2 个变体拿第 1 个变体的结果也算过，等于没换输入"


def test_a_missing_deliverable_variable_is_not_silently_an_empty_table() -> None:
    """需求点名的变量压根没产出时，rows 是 None——当成空表会把它算成「筛出 0 行」。"""
    failures, _ = _judge_variant(_table_case(), 0, _OK_RUN, {})
    assert failures and "rows" in failures[0]


def test_a_failed_acceptance_audit_fails_the_variant_even_when_rows_match() -> None:
    """流程自己的验收契约没过，就算行对了也不算过：契约是流程对自己声明的后置条件。"""
    run = {"status": "success", "acceptance_audit": {"passed": False, "issues": ["缺少必填字段"]}}
    failures, _ = _judge_variant(_table_case(), 0, run, {"rows": _V0_ROWS})
    assert failures and "验收契约未通过" in failures[0]


def test_scalar_readback_is_compared_day_by_day_against_the_input_it_belongs_to() -> None:
    """留出页不许读，判据只能靠回读本身：空值、月错、日错、起止颠倒、落进同一个框。"""
    case = _scalar_case()
    assert _judge_variant(case, 0, _OK_RUN,
                          {"start_readback": "2026-06-01", "end_readback": "2026-06-30"})[0] == []

    empty, _ = _judge_variant(case, 0, _OK_RUN, {"start_readback": "", "end_readback": "2026-06-30"})
    assert empty and "start_readback" in empty[0]

    wrong_month, _ = _judge_variant(case, 1, _OK_RUN,
                                    {"start_readback": "2025-04-02", "end_readback": "2025-03-20"})
    assert any("start_readback" in f and "2025-03-02" in f for f in wrong_month), wrong_month

    wrong_day, _ = _judge_variant(case, 1, _OK_RUN,
                                  {"start_readback": "2025-03-03", "end_readback": "2025-03-20"})
    assert any("start_readback" in f and "2025-03-02" in f for f in wrong_day), wrong_day

    swapped, _ = _judge_variant(case, 1, _OK_RUN,
                                {"start_readback": "2025-03-20", "end_readback": "2025-03-02"})
    assert any("顺序颠倒" in f for f in swapped), swapped

    same, _ = _judge_variant(case, 0, _OK_RUN,
                             {"start_readback": "2026-06-01", "end_readback": "2026-06-01"})
    assert any("同一个框" in f for f in same), same


def test_a_readback_the_case_never_declared_a_format_for_is_unverifiable() -> None:
    """组件把日期改写成没声明的写法时只能报「无法验证」，不能猜数字组的日月顺序。"""
    failures, _ = _judge_variant(_scalar_case(), 1, _OK_RUN,
                                 {"start_readback": "02/03/2025", "end_readback": "2025-03-20"})
    assert any("无法验证" in f for f in failures), failures


def test_a_readback_in_another_declared_format_still_passes() -> None:
    """案例允许的几种写法之间不能互相判错：比的是哪一天，不是哪串文本。"""
    assert _judge_variant(_scalar_case(), 1, _OK_RUN,
                          {"start_readback": "2025/03/02", "end_readback": "2025年03月20日"})[0] == []


def test_parse_date_only_accepts_the_declared_formats() -> None:
    """兜底解析必须不存在：允许 %d/%m/%Y，03/02 与 02/03 就互为对方，日月填反会判成通过。"""
    assert _parse_date("2026-06-02", ("%Y-%m-%d",)) == date(2026, 6, 2)
    assert _parse_date("2026/06/02", ("%Y-%m-%d",)) is None
    assert _parse_date("02/06/2026", ("%Y-%m-%d", "%Y/%m/%d")) is None
    assert _parse_date("", ("%Y-%m-%d",)) is None


def test_row_values_align_dict_and_list_rows_and_name_the_absent_fields() -> None:
    """单列表格按 list[list] 交出，多列按 list[dict]：两种都要摊平到 row_fields。"""
    fields = ("单号", "日期", "金额")
    assert _row_values({"单号": "B-02", "日期": "2026-06-02", "金额": "200"}, fields) == (
        ["B-02", "2026-06-02", "200"], [],
    )
    assert _row_values({"单号": "B-02"}, fields) == (["B-02", "", ""], ["日期", "金额"])
    assert _row_values(["P1-A"], ("单号",)) == (["P1-A"], [])
    assert _row_values("P1-A", ("单号",)) == (["P1-A"], [])
    assert _row_values(["B-02", "2026-06-02"], fields) == (["B-02", "2026-06-02", ""], ["金额"])


class _FakeExecutor:
    """只实现 run_flow 与 get_task 的假执行器：判分链路不需要真浏览器。"""

    def __init__(self, per_variant: list[dict[str, Any]]) -> None:
        self._per_variant = per_variant
        self.runs: list[dict[str, Any]] = []
        self._task_manager = self

    async def execute(self, name: str, args: dict[str, Any], progress_sink: Any = None,
                      change_context: Any = None) -> dict[str, Any]:
        if name == "create_flow":
            return {"flow_id": "f1", "lint_clean": True}
        if name == "get_flow":
            return {"flow_id": args.get("flow_id"), "nodes": []}
        assert name == "run_flow", name
        self.runs.append(args)
        return {"task_id": f"t{len(self.runs)}", **_OK_RUN}

    async def get_task(self, task_id: str) -> Any:
        index = int(task_id[1:]) - 1
        values = self._per_variant[index]
        snapshots = [type("S", (), {"name": k, "value": v})() for k, v in values.items()]
        return type("T", (), {"variables": snapshots})()


async def test_replay_passes_each_variant_as_run_variables() -> None:
    """变体必须作为运行输入变量传进去，而不是改流程定义：重放的是同一条流程。"""
    case = _table_case()
    executor = _FakeExecutor([
        {"rows": json.dumps(_V0_ROWS, ensure_ascii=False)},
        {"rows": json.dumps([
            {"单号": "B-01", "日期": "2026-05-28", "金额": "100"},
            {"单号": "B-02", "日期": "2026-06-02", "金额": "200"},
        ], ensure_ascii=False)},
    ])
    _, verdicts = await _replay_variants(executor, case, "f1")  # type: ignore[arg-type]
    assert [run["variables"] for run in executor.runs] == case.variants
    assert all(v["passed"] for v in verdicts), verdicts


async def test_a_hardcoded_date_is_caught_because_the_variants_change_the_day() -> None:
    """把日期写死在节点里的流程，必须在第 2 个变体上挂掉。

    变体只换年份时，「回读值含本次输入的年份」那种判据会给写死的日期一路绿灯——判据逐项比
    年月日之后，第 2 组换的是整整一天，写死的值躲不过去。
    """
    case = _scalar_case()
    assert {v["start_date"] for v in case.variants} == {"2026-06-01", "2025-03-02"}
    frozen = {"start_readback": "2026-06-01", "end_readback": "2026-06-30"}
    executor = _FakeExecutor([dict(frozen), dict(frozen)])
    _, verdicts = await _replay_variants(executor, case, "f1")  # type: ignore[arg-type]
    assert verdicts[0]["passed"] is True
    assert verdicts[1]["passed"] is False
    assert any("2025-03-02" in f for f in verdicts[1]["failures"]), verdicts[1]["failures"]


def test_the_recording_proxy_keeps_the_executor_signature_byte_for_byte() -> None:
    """签名不一致时编排层把 TypeError 当成「工具执行失败」吞掉：
    评测会把接线缺陷报成模型能力问题，而且每个案例都报。"""
    assert (inspect.signature(RecordingExecutor.execute).parameters.keys()
            == inspect.signature(RpaToolExecutor.execute).parameters.keys())


async def test_the_proxy_separates_model_calls_from_the_platform_state_block() -> None:
    """平台每轮都会 get_flow 重建状态块。混进 calls 会让「模型调了几次工具」和 token
    成本全部失真，也会让 saved_flow_id 认到平台的读取上。"""
    recorder = RecordingExecutor(_FakeExecutor([]))  # type: ignore[arg-type]
    recorder.recording = True
    await recorder.execute("create_flow", {"name": "x"})
    await recorder.execute("get_flow", {"flow_id": "f1"})

    assert [name for name, _ in recorder.calls] == ["create_flow"]
    assert [name for name, _ in recorder.platform_calls] == ["get_flow"]
    # create_flow 的 id 只在返回里：模型建完流程不再碰它时，args 里一个 flow_id 都没有
    assert recorder.saved_flow_id() == "f1"

    recorder.recording = False
    await recorder.execute("run_flow", {"flow_id": "f1"})
    assert len(recorder.calls) == 1, "重放阶段的调用不能算成模型这一轮的工具调用"


async def test_the_proxy_passes_everything_else_through_to_the_real_executor() -> None:
    inner = _FakeExecutor([])
    recorder = RecordingExecutor(inner)  # type: ignore[arg-type]
    assert recorder._task_manager is inner


class _FakeOrchestrator:
    """假编排层：只按事件契约吐 text/usage/error，并按需替模型调一次工具。"""

    def __init__(self, executor: Any, tool_calls: list[tuple[str, dict[str, Any]]]) -> None:
        self._executor = executor
        self._tool_calls = tool_calls

    async def stream(self, messages: list[dict[str, Any]], model: str, flow_id: Any = None) -> Any:
        assert messages and model
        for name, args in self._tool_calls:
            await self._executor.execute(name, args)
        yield {"type": "text", "delta": "看完了"}
        yield {"type": "usage", "usage": {"rounds": 3, "prompt_tokens": 11, "completion_tokens": 7}}


async def test_a_model_that_never_saved_a_flow_is_scored_as_such_not_as_wrong_data() -> None:
    """一次流程都没存下来，报的必须是 no_flow_saved：报成「数据不符」会让人去查选择器。"""
    inner = _FakeExecutor([])
    recorder = RecordingExecutor(inner)  # type: ignore[arg-type]
    result = await run_model_case(
        _FakeOrchestrator(recorder, []), recorder, "m", _table_case()  # type: ignore[arg-type]
    )
    assert result["passed"] is False
    assert result["stage"] == "no_flow_saved"
    assert inner.runs == [], "没有流程可跑，不能糊里糊涂跑一次空的"


class _ErroringOrchestrator:
    """只吐 error 事件的假编排层：复现上游限流/配额用尽时一个回合都没给的情形。"""

    def __init__(self, errors: list[str], usage: dict[str, Any] | None = None) -> None:
        self._errors = errors
        self._usage = usage

    async def stream(self, messages: list[dict[str, Any]], model: str, flow_id: Any = None) -> Any:
        assert messages and model
        if self._usage is not None:
            yield {"type": "usage", "usage": self._usage}
        for message in self._errors:
            yield {"type": "error", "message": message}


async def test_an_upstream_that_never_answered_is_marked_not_run_not_no_flow_saved() -> None:
    """上游一个回合都没给时阶段必须是 model_unreachable：报成 no_flow_saved 会把
    「中转挂了」说成「模型不会建流程」，人会去翻提示词和工具 schema。"""
    inner = _FakeExecutor([])
    recorder = RecordingExecutor(inner)  # type: ignore[arg-type]
    orchestrator = _ErroringOrchestrator(['code=429 reason="MONTHLY_LIMIT_EXCEEDED"'])

    result = await run_model_case(orchestrator, recorder, "m", _table_case())  # type: ignore[arg-type]

    assert result["stage"] == "model_unreachable"
    assert result["not_run"] is True
    assert result["metrics"]["rounds"] == 0
    assert result["model_errors"] == ['code=429 reason="MONTHLY_LIMIT_EXCEEDED"']
    assert inner.runs == []


async def test_an_error_after_real_rounds_is_still_a_real_failure() -> None:
    """模型答过、只是收尾时才撞上配额：案例跑过、有数据可判，不能算「未运行」。

    判据只看有没有回合，不解析错误文本——中转把「没有这个模型」也写成 rate-limited，
    按关键词分流会把两种处置（等配额 / 永远等不到）判反。
    """
    inner = _FakeExecutor([])
    recorder = RecordingExecutor(inner)  # type: ignore[arg-type]
    orchestrator = _ErroringOrchestrator(
        ["monthly usage limit exceeded"], usage={"rounds": 13, "prompt_tokens": 290182},
    )

    result = await run_model_case(orchestrator, recorder, "m", _table_case())  # type: ignore[arg-type]

    assert result.get("not_run") is None
    assert result["stage"] == "no_flow_saved"


async def test_the_model_case_replays_the_saved_flow_and_reports_cost() -> None:
    """模型存下流程后：按变体重放、判分，并带回本轮的轮次与 token 成本。"""
    inner = _FakeExecutor([
        {"rows": json.dumps(_V0_ROWS, ensure_ascii=False)},
        {"rows": json.dumps([
            {"单号": "B-01", "日期": "2026-05-28", "金额": "100"},
            {"单号": "B-02", "日期": "2026-06-02", "金额": "200"},
        ], ensure_ascii=False)},
    ])
    recorder = RecordingExecutor(inner)  # type: ignore[arg-type]
    orchestrator = _FakeOrchestrator(recorder, [("create_flow", {"name": "x"})])

    result = await run_model_case(orchestrator, recorder, "m", _table_case())  # type: ignore[arg-type]

    assert result["passed"] is False
    assert result["replay_passed"] is True
    assert result["model_execution"]["passed"] is False
    assert result["variants_passed"] == 2 and result["flow_id"] == "f1"
    assert result["model_tool_calls"] == ["create_flow"]
    assert result["metrics"]["rounds"] == 3
    assert result["metrics"]["prompt_tokens"] == 11


def test_fixture_server_serves_http_and_closes_after_error() -> None:
    with pytest.raises(RuntimeError, match="stop"):
        with fixture_server() as base_url:
            url = page_url("filter_enter_commit.html", base_url)
            with urlopen(url, timeout=2) as response:
                assert response.status == 200
                assert b"q-start" in response.read()
            assert _model_messages(_table_case(), url)
            port = urlsplit(url).port
            raise RuntimeError("stop")
    with socket.socket() as connection:
        assert connection.connect_ex(("127.0.0.1", port)) != 0


def test_preflight_rejects_an_unrecognized_url_before_model_call() -> None:
    with pytest.raises(ValueError, match="URL"):
        _model_messages(_table_case(), "file:///tmp/filter.html")


async def test_recorder_keeps_failed_and_cancelled_attempts_and_original_exception() -> None:
    error = TypeError("unexpected keyword argument 'arguments'")

    class Failing:
        async def execute(self, name: str, args: Any, *rest: Any) -> Any:
            args.clear()
            raise error

    recorder = RecordingExecutor(Failing())
    recorder.recording = True
    with pytest.raises(TypeError) as caught:
        await recorder.execute("run_flow", {"flow_id": "f1"})
    assert caught.value is error
    assert recorder.calls == [("run_flow", {"flow_id": "f1"})]
    assert recorder.evidence[0]["outcome"] == "exception"
    assert recorder.evidence[0]["exception"]["type"] == "TypeError"
    assert recorder.saved_flow_id() == "", "failed calls cannot claim a saved flow"

    error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await recorder.execute("run_flow", {"flow_id": "f1"})
    assert len(recorder.calls) == 2
    assert recorder.evidence[1]["exception"]["type"] == "CancelledError"


async def test_recorder_retains_structured_failure_without_payloads() -> None:
    class Failed:
        async def execute(self, *args: Any) -> Any:
            return {"status": "error", "error": "invalid_arguments", "screenshot": "base64-secret"}

    recorder = RecordingExecutor(Failed())
    recorder.recording = True
    await recorder.execute("run_flow", {"arguments": {"password": "secret"}})
    assert recorder.evidence[0]["outcome"] == "returned"
    assert recorder.evidence[0]["result"]["status"] == "error"
    assert recorder.evidence[0]["result"]["error"] == "invalid_arguments"
    assert "base64-secret" not in json.dumps(recorder.evidence)
    assert "password" not in json.dumps(recorder.evidence)


async def test_model_e2e_requires_its_own_successful_audited_run() -> None:
    rows = [{"rows": json.dumps(_V0_ROWS, ensure_ascii=False)},
            {"rows": json.dumps(_V0_ROWS, ensure_ascii=False)},
            {"rows": json.dumps([
                {"单号": "B-01", "日期": "2026-05-28", "金额": "100"},
                {"单号": "B-02", "日期": "2026-06-02", "金额": "200"},
            ], ensure_ascii=False)}]
    recorder = RecordingExecutor(_FakeExecutor(rows))
    orchestrator = _FakeOrchestrator(recorder, [
        ("create_flow", {"name": "x"}), ("run_flow", {"flow_id": "f1"}),
    ])
    result = await run_model_case(orchestrator, recorder, "m", _table_case())
    assert result["passed"] is True
    assert result["replay_passed"] is True
    assert result["model_execution"]["passed"] is True
    assert result["model_execution"]["task_id"] == "t1"
    assert result["metrics"]["tool_calls"] == 2
    assert len(result["model_tool_evidence"]) == 2


@pytest.mark.parametrize("result", [
    {"status": "success", "task_id": "t1"},
    {"status": "success", "acceptance_audit": {"passed": True}},
    {"status": "success", "task_id": "t1", "acceptance_audit": {"passed": False}},
    {"status": "error", "task_id": "t1", "acceptance_audit": {"passed": True}},
])
async def test_model_execution_rejects_missing_or_failed_evidence(result: dict[str, Any]) -> None:
    recorder = RecordingExecutor(_FakeExecutor([]))
    recorder.evidence = [{"name": "run_flow", "flow_id": "f1", "outcome": "returned", "result": result}]
    assert (await _judge_model_execution(recorder, _table_case(), "f1"))["passed"] is False


async def test_model_execution_invalidates_a_run_when_flow_changes_afterwards() -> None:
    recorder = RecordingExecutor(_FakeExecutor([]))
    recorder.evidence = [
        {"name": "run_flow", "flow_id": "f1", "outcome": "returned",
         "result": {"task_id": "t1", **_OK_RUN}},
        {"name": "apply_node_fix", "flow_id": "f1", "outcome": "returned", "result": {"status": "success"}},
    ]
    verdict = await _judge_model_execution(recorder, _table_case(), "f1")
    assert verdict["passed"] is False
    assert "修改流程后" in verdict["failures"][0]


async def test_model_execution_checks_actual_task_data_even_with_a_passing_audit() -> None:
    recorder = RecordingExecutor(_FakeExecutor([{"rows": "[]"}]))
    recorder.evidence = [{"name": "run_flow", "flow_id": "f1", "outcome": "returned",
                          "result": {"task_id": "t1", **_OK_RUN}}]
    verdict = await _judge_model_execution(recorder, _table_case(), "f1")
    assert verdict["passed"] is False
    assert verdict["missing"] == ["B-02", "B-03"]


def test_preflight_rejects_empty_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("evals.run_e2e._tool_schemas_for_round", lambda *args: [])
    with pytest.raises(ValueError, match="工具集合为空"):
        _model_messages(_table_case(), "http://127.0.0.1:8123/filter.html")


async def test_recorder_redacts_credentials_in_error_evidence() -> None:
    class Failed:
        async def execute(self, *args: Any) -> Any:
            return {"status": "error", "error": "Bearer test-token sk-testsecret"}

    recorder = RecordingExecutor(Failed())
    recorder.recording = True
    result = await recorder.execute("run_flow", {"flow_id": "f1"})
    assert result["error"] == "Bearer test-token sk-testsecret"
    assert recorder.evidence[0]["result"]["error"] == "[REDACTED] [REDACTED]"


async def test_blocked_write_preserves_previous_run_evidence_and_saved_flow() -> None:
    recorder = RecordingExecutor(_FakeExecutor([{"rows": json.dumps(_V0_ROWS)}]))
    recorder.evidence = [
        {"name": "run_flow", "flow_id": "f1", "outcome": "returned",
         "result": {"task_id": "t1", **_OK_RUN}},
        {"name": "update_flow", "flow_id": "f1", "outcome": "returned",
         "result": {"status": "blocked_credential_values"}},
        {"name": "update_flow", "flow_id": "other", "outcome": "returned",
         "result": {"status": "blocked_credential_values"}},
    ]
    assert recorder.saved_flow_id() == "f1"
    assert (await _judge_model_execution(recorder, _table_case(), "f1"))["passed"] is True


async def test_recorder_copies_safe_argument_diagnostics() -> None:
    result = {"status": "error", "error": "invalid_arguments",
              "issues": [{"path": [], "rule": "required", "expected": ["flow_id"]}],
              "expected_parameters": {"required": ["flow_id"]}}

    class Failed:
        async def execute(self, *args: Any) -> Any:
            return result

    recorder = RecordingExecutor(Failed())
    recorder.recording = True
    await recorder.execute("run_flow", {})
    result["issues"].clear()
    result["expected_parameters"].clear()
    assert recorder.evidence[0]["result"]["issues"] == [
        {"path": [], "rule": "required", "expected": ["flow_id"]},
    ]
    assert recorder.evidence[0]["result"]["expected_parameters"] == {"required": ["flow_id"]}


async def test_blocked_run_evidence_names_the_findings_that_blocked_it() -> None:
    """报告只写 status='blocked_lint' 等于说「被拦了」不说「拦在哪」：
    判节点配置和连线得回头翻隔离库重算 lint，评测结论就不再来自报告本身。"""
    finding = {
        "severity": "error", "issue": "forked_path_downstream_swallowed", "node_id": "n4",
        "node_title": "读取校验结果", "message": "含页面数据的说明", "fix": "含修正建议的说明",
    }

    class Blocked:
        async def execute(self, *args: Any) -> Any:
            return {"status": "blocked_lint", "task_id": "", "lint_findings": [finding]}

    recorder = RecordingExecutor(Blocked())
    recorder.recording = True
    await recorder.execute("run_flow", {"flow_id": "f1"})
    kept = recorder.evidence[0]["result"]["lint_findings"]
    assert kept == [{"severity": "error", "issue": "forked_path_downstream_swallowed", "node_id": "n4"}]
    assert "message" not in kept[0] and "fix" not in kept[0]


def test_report_says_which_model_actually_served_the_run() -> None:
    """中转没有目标模型时会按名称模糊匹配到另一个：报告只写请求的 model，
    等于把这次的通过率挂到评测时根本没跑的模型头上。"""
    from evals.run_e2e import build_report

    substituted = build_report(
        model="agentrouter/claude-opus-5", ran_online=True, results=[], self_check=[],
        env={}, served_by="claude-opus-5-20260101",
    )
    assert substituted["model_substituted"] is True
    assert substituted["served_by"] == "claude-opus-5-20260101"

    same = build_report(
        model="agentrouter/claude-opus-5", ran_online=True, results=[], self_check=[],
        env={}, served_by="claude-opus-5",
    )
    assert same["model_substituted"] is False

    # 没连上中转（served_by 为空）时不能报成「被替换」，那会把没跑起来读成换了模型
    unknown = build_report(
        model="claude-opus-5", ran_online=False, results=[], self_check=[], env={},
    )
    assert unknown["model_substituted"] is False


def _not_run_case(name: str, error: str) -> dict[str, Any]:
    """上游一个回合都没给的案例，形状与 _run_model_case 的 model_unreachable 分支一致。"""
    return {
        "case": name, "page": "p.html", "stage": "model_unreachable", "passed": False,
        "not_run": True, "variants_passed": 0, "variants_total": 2, "verdicts": [],
        "replay_passed": False, "model_errors": [error],
        "model_execution": {"passed": False, "failures": ["上游未返回任何回合"]},
    }


def _passed_case(name: str) -> dict[str, Any]:
    verdicts = [{"passed": True, "failures": []}, {"passed": True, "failures": []}]
    return {
        "case": name, "page": "p.html", "stage": "done", "passed": True,
        "variants_passed": 2, "variants_total": 2, "verdicts": verdicts,
        "replay_passed": True, "model_errors": [],
        "model_execution": {"passed": True, "failures": []},
    }


def test_a_case_the_upstream_never_answered_is_not_counted_as_a_failure() -> None:
    """上游限流/配额用尽时案例根本没跑过：计进分母等于让「上游挂了」冒充「模型没通过」。

    实测 gpt-5.5 那轮 4 个案例里有 1 个是 429 MONTHLY_LIMIT_EXCEEDED、rounds=0、
    tokens=0，报告却算成 2/4——真实分母是 3，差的那一个从未执行。
    """
    from evals.run_e2e import build_report

    report = build_report(
        model="gpt-5.5", ran_online=True, self_check=[], env={},
        results=[_passed_case("a"), _passed_case("b"),
                 _not_run_case("c", 'code=429 reason="MONTHLY_LIMIT_EXCEEDED"')],
    )
    summary = report["model_e2e"]
    assert summary["cases_total"] == 2
    assert summary["cases_passed"] == 2
    assert summary["cases_not_run"] == 1
    # 变体分母同样不能含没跑过的案例
    assert summary["variants_total"] == 4


def test_every_case_unreachable_reports_not_run_rather_than_a_zero_score() -> None:
    """一个案例都没跑起来时整轮是「未运行」，不是「0 分」。"""
    from evals.run_e2e import build_report

    report = build_report(
        model="gpt-5.5", ran_online=False, self_check=[], env={},
        not_run_reason="上游未返回任何回合：All available accounts are currently rate-limited.",
        results=[_not_run_case("a", "rate-limited"), _not_run_case("b", "rate-limited")],
    )
    assert report["model_e2e"]["cases_total"] == 0
    assert report["model_e2e"]["cases_not_run"] == 2
    assert report["online_ran"] is False
    assert "rate-limited" in report["not_run_reason"]


def test_a_case_that_ran_and_failed_still_counts_even_with_a_late_upstream_error() -> None:
    """配额在收尾时才打断的案例跑过、有数据可判，必须留在分母里。

    gpt-5.5 的 pagination_sweep 就是这种：rounds=13、流程建好跑成功，最后一轮才撞配额。
    误判成「未运行」会把真实的多抓一行洗掉。
    """
    from evals.run_e2e import build_report

    late = _passed_case("pagination_sweep")
    late.update(passed=False, variants_passed=1, model_errors=["monthly usage limit exceeded"],
                verdicts=[{"passed": True, "failures": []},
                          {"passed": False, "failures": ["多了这些记录：['P3-A']"], "extra": ["P3-A"]}])
    report = build_report(model="gpt-5.5", ran_online=True, self_check=[], env={}, results=[late])
    assert report["model_e2e"]["cases_total"] == 1
    assert report["model_e2e"]["cases_not_run"] == 0
    assert report["model_e2e"]["variants_extra_rows"] == 1


@pytest.mark.parametrize("event", [
    {"type": "text", "delta": "已开始分析"},
    {"type": "thinking", "delta": "检查页面"},
    {"type": "tool_start", "tool": "inspect_page", "call_id": "c1"},
])
async def test_partial_first_response_is_interrupted_not_unreachable(event):
    class Interrupted:
        async def stream(self, **kwargs):
            yield event
            yield {"type": "error", "message": "connection reset"}
    recorder = RecordingExecutor(_FakeExecutor([]))
    result = await run_model_case(Interrupted(), recorder, "m", _table_case())
    assert result["stage"] == "model_response_interrupted"
    assert not result.get("not_run")
    assert result["passed"] is False
