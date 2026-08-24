"""验收由平台跑，不由模型发起。

模型手上没有审计工具（见 tests/test_ai_prompts.py 的 PLATFORM_ONLY_TOOLS），所以
「跑完之后有没有人验」不再取决于它这一轮想不想调。这里守的是这条设计的两个落点：
run_flow 拿到终态 success 时结论必须已经在返回里，以及状态块每轮重算时能补上
运行当时还拿不到的结论。

契约条款本身的判据在 tests/test_acceptance_audit.py，这里只验「谁在什么时候算」。
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.models.schemas import (
    FlowAcceptanceContract,
    FlowSnapshot,
    RuntimeProgress,
    RuntimeVariableSnapshot,
    TaskSnapshot,
)
from app.services.ai_checks import audit_run
from app.services.ai_tools import RpaToolExecutor
from app.services.execution_evidence import definition_digest

_DEFINITION: dict[str, Any] = {
    "nodes": [
        {"id": "start", "type": "start"},
        {"id": "n_open", "type": "browser.open", "targetUrl": "https://shop.test/orders"},
        {
            "id": "n_extract",
            "type": "browser.extract",
            "selector": "table tbody tr",
            "extractMode": "table",
            "outputVariable": "order_rows",
        },
        {"id": "end", "type": "end"},
    ],
    "edges": [
        {"source": "start", "target": "n_open"},
        {"source": "n_open", "target": "n_extract"},
        {"source": "n_extract", "target": "end"},
    ],
}

_CONTRACT = FlowAcceptanceContract.model_validate({
    "requirements": [{
        "id": "orders",
        "description": "抓取订单列表的编号与状态",
        "sourceKind": "user",
        "sourceQuote": "抓订单列表的编号和状态",
        "confidence": 1,
        "confirmed": True,
    }],
    "deliverables": [{
        "id": "order_table",
        "variable": "order_rows",
        "kind": "table",
        "minRows": 2,
        "requiredFields": ["编号", "状态"],
        "requirementIds": ["orders"],
    }],
})


def _flow(revision: int = 1) -> FlowSnapshot:
    now = datetime.now(UTC)
    return FlowSnapshot(
        flowId="flow-1",
        name="订单流程",
        version="v1.0.0",
        status="active",
        definition=_DEFINITION,
        inputVariables=[],
        acceptanceContract=_CONTRACT,
        revision=revision,
        createdAt=now,
        updatedAt=now,
    )


def _task(rows: Any, *, task_id: str, status: str = "success") -> TaskSnapshot:
    now = datetime.now(UTC)
    return TaskSnapshot(
        taskId=task_id,
        flowId="flow-1",
        flowName="订单流程",
        mode="run",
        status=status,  # type: ignore[arg-type]
        progress=RuntimeProgress(currentStep=4, totalSteps=4, percent=100, elapsedMs=2000),
        flowRevision=1,
        definitionDigest=definition_digest(_DEFINITION),
        acceptanceContract=_CONTRACT,
        variables=[RuntimeVariableSnapshot(
            name="order_rows", type="List", value=json.dumps(rows, ensure_ascii=False),
        )],
        createdAt=now,
        updatedAt=now,
    )


class _TaskManager:
    """跑完即成功：这里要验的是「审计有没有被跑」，不是运行本身。"""

    def __init__(self, task: TaskSnapshot) -> None:
        self.task = task

    async def start_task(self, request: Any) -> TaskSnapshot:
        return self.task

    async def get_task(self, task_id: str) -> TaskSnapshot | None:
        return self.task if task_id == self.task.task_id else None

    async def list_tasks(self, **_kwargs: Any) -> list[TaskSnapshot]:
        return []

    def is_extension_connected(self) -> bool:
        return False


def _executor(task: TaskSnapshot, *, revision: int = 1) -> RpaToolExecutor:
    class _FlowService:
        async def get_flow(self, _flow_id: str) -> FlowSnapshot:
            return _flow(revision)

    return RpaToolExecutor(flow_service=_FlowService(), task_manager=_TaskManager(task))  # type: ignore[arg-type]


_GOOD_ROWS = [{"编号": "A-001", "状态": "已完成"}, {"编号": "A-002", "状态": "待处理"}]


async def test_run_flow_hands_back_the_audit_together_with_the_run_result() -> None:
    """success 只说明节点没抛异常，产物合不合格必须同一轮就有结论。

    过去这一步要模型自己再调一次审计工具；不调就等于验收通过，而「不调」正是最省
    token 的走法。
    """
    executor = _executor(_task(_GOOD_ROWS, task_id="task-ok"))

    result = await executor._run_flow("flow-1")

    assert result["status"] == "success"
    audit = result["acceptance_audit"]
    assert audit["passed"] is True
    assert audit["task_id"] == "task-ok"


async def test_failed_audit_arrives_with_the_node_to_change() -> None:
    """不合格结论要带出「该动哪个节点」：只说交付物不合格，模型仍要靠猜，猜错一次就是一次完整重跑。"""
    rows = [{"编号": "A-001"}, {"编号": "A-002"}]  # 缺 状态 列
    executor = _executor(_task(rows, task_id="task-missing-field"))

    result = await executor._run_flow("flow-1")

    audit = result["acceptance_audit"]
    assert audit["passed"] is False
    assert [issue["issue"] for issue in audit["issues"]] == ["required_fields_missing"]
    assert audit["repair_plan"][0]["action"] == "extract_the_missing_fields"


async def test_a_run_older_than_the_current_definition_is_not_evidence() -> None:
    """流程改过之后，上一次运行的产物验的是另一份定义——状态块每轮重算就是为了让这个转变可见。"""
    executor = _executor(_task(_GOOD_ROWS, task_id="task-ok"), revision=3)

    audit = await executor.execute("audit_run", {"task_id": "task-ok"})

    assert audit["passed"] is False
    assert [issue["issue"] for issue in audit["issues"]] == ["stale_run_evidence"]
    assert audit["repair_plan"][0]["action"] == "rerun_current_revision"


def test_garbage_rows_fail_even_when_every_contract_clause_passes() -> None:
    """行数够、字段齐、类型对，四条契约条款全过，交上来的还是一堆没法用的行。

    契约描述的是「行里的字段对不对」，没有条款能表达「这些行本身是垃圾」，也不该有——
    表头被当成数据行、半空行、整表被摊平，都是抽取形态的缺陷，不是业务条件的缺陷。
    """
    header_echo = [
        {"编号": "编号", "状态": "状态"},
        {"编号": "A-001", "状态": "已完成"},
        {"编号": "A-002", "状态": "待处理"},
    ]

    audit = audit_run(_task(header_echo, task_id="task-header"), _flow())

    assert audit["passed"] is False
    assert [issue["issue"] for issue in audit["issues"]] == ["header_row_as_data"]
    # 缺陷落在哪个交付物上必须说清，否则多交付物的流程里模型只能挨个试
    assert audit["issues"][0]["deliverable_id"] == "order_table"
    assert audit["repair_plan"][0]["action"] == "filter_header_rows"


def test_an_empty_required_table_cannot_pass_as_delivered() -> None:
    """minRows 是可选项：没声明的表，空数组会一条契约条款都不违反。"""
    contract = FlowAcceptanceContract.model_validate({
        "deliverables": [{"id": "order_table", "variable": "order_rows", "kind": "table"}],
    })
    task = _task([], task_id="task-empty")
    task = task.model_copy(update={"acceptance_contract": contract})

    audit = audit_run(task, _flow())

    assert audit["passed"] is False
    assert [issue["issue"] for issue in audit["issues"]] == ["empty_rows"]


async def test_state_block_recomputes_the_audit_for_a_run_that_finished_later() -> None:
    """run_flow 轮询到 90s 就返回 timeout——那一刻的返回里不可能有验收结论。

    模型手上没有审计工具，这里不补就永远没有人补，「跑完了但没人验」会一路当成通过。
    """
    from app.services.ai_flow_state import build_flow_state, render_flow_state

    task = _task(_GOOD_ROWS, task_id="task-late")
    executor = _executor(task)
    timed_out = {"task_id": "task-late", "status": "timeout"}

    state = await build_flow_state(executor, "flow-1", timed_out)

    audit = state.last_run["acceptance_audit"]
    assert state.last_run["status"] == "success"
    assert audit["passed"] is True
    assert "验收：通过" in (render_flow_state(state) or "")


async def test_state_block_flags_an_incomplete_contract_before_a_run_is_wasted() -> None:
    """契约不完整时 run_flow 会在启动浏览器前拒掉——这个拒绝本来只能靠真跑一次才知道。

    判据与 run_flow 用的是同一个 contract_validation_errors，两处结论不可能分岔。
    """
    from app.services.ai_flow_state import build_flow_state

    class _FlowService:
        async def get_flow(self, _flow_id: str) -> FlowSnapshot:
            # 交付变量没有任何节点产出，run_flow 会据此在起跑前拒掉
            return _flow().model_copy(update={
                "acceptance_contract": FlowAcceptanceContract.model_validate({
                    "deliverables": [{"id": "d", "variable": "nobody_produces_this", "kind": "table"}],
                }),
            })

    executor = RpaToolExecutor(  # type: ignore[arg-type]
        flow_service=_FlowService(), task_manager=_TaskManager(_task(_GOOD_ROWS, task_id="t")),
    )

    state = await build_flow_state(executor, "flow-1")

    incomplete = [f for f in state.findings if f["issue"] == "acceptance_contract_incomplete"]
    assert incomplete and incomplete[0]["severity"] == "error"
    assert "set_acceptance_contract" in incomplete[0]["fix"]

    # 契约补齐后这条诊断必须消失，否则模型改对了也看不到变化，只会以为改法无效
    clean = await build_flow_state(_executor(_task(_GOOD_ROWS, task_id="t")), "flow-1")
    assert [f for f in clean.findings if f["issue"] == "acceptance_contract_incomplete"] == []
