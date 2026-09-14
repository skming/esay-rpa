"""运行结束后的统一审计入口。

run_flow 成功返回时审计；先前挂起的任务完成后，由状态块补审。
审计前检查任务与当前流程版本、定义是否一致，防止旧产物被用作当前定义的证据。"""
from __future__ import annotations

from typing import Any

from app.core import storage
from app.services.acceptance_audit import audit_acceptance_contract, resolved_min_rows
from app.services.ai_tools.diagnostics import (
    _build_quality_repair_plan,
    _check_structured_rows,
    _coerce_table_rows,
    _find_incomplete_sweeps,
    _find_ineffective_transforms,
    _parse_runtime_value,
)
from app.services.execution_evidence import definition_digest

# 审计要读产物文件，而状态块每轮都会重算一次。按 (任务, 定义摘要, 流程版本) 缓存：
# 这三者不变，结论必然不变；任一变化都必须重算（改完流程后旧产物应当被判为过期证据）。
_CACHE_LIMIT = 32
_audit_cache: dict[tuple[str, str, str], dict[str, Any]] = {}


def audit_run(task: Any, flow: Any) -> dict[str, Any]:
    """审计一次运行的产物。调用方保证 `task.status == "success"`。"""
    key = (
        str(task.task_id),
        str(task.definition_digest or ""),
        str(getattr(flow, "revision", "") if flow is not None else ""),
    )
    cached = _audit_cache.get(key)
    if cached is not None:
        return cached
    result = _audit(task, flow)
    _audit_cache[key] = result
    while len(_audit_cache) > _CACHE_LIMIT:
        _audit_cache.pop(next(iter(_audit_cache)), None)
    return result


def _audit(task: Any, flow: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "task_id": task.task_id,
        "flow_revision": task.flow_revision,
        "definition_digest": task.definition_digest,
    }
    if flow is None:
        return _verdict(base, [{
            "issue": "orphaned_run_evidence",
            "message": "任务关联的流程已不存在，无法证明该产物对应当前可执行定义。",
        }], [], [])

    if task.flow_revision is not None and task.flow_revision != flow.revision:
        return _verdict(base, [{
            "issue": "stale_run_evidence",
            "message": (
                f"这次运行的是 revision {task.flow_revision}，流程当前已是 revision {flow.revision}。"
                "旧产物不能验收新定义，必须重新运行当前流程。"
            ),
        }], [], [])

    if task.definition_digest != definition_digest(flow.definition):
        return _verdict(base, [{
            "issue": "definition_digest_mismatch",
            "message": "流程 revision 未变化但定义摘要不同，拒绝复用可能被旁路修改的运行证据。",
        }], [], [])

    variables = {snap.name: _parse_runtime_value(snap.value) for snap in (task.variables or [])}

    if not task.acceptance_contract.deliverables:
        # run_flow 与 flow_runner 都在启动前拒掉了没有完整契约的流程，正常不会走到这里。
        # 但「契约为空」若被当成「没有要求，审计通过」，一次绕过就把整套验收变成空转。
        return _verdict(base, [{
            "issue": "acceptance_contract_missing",
            "message": "这次运行没有携带交付验收契约，无法确定应当验收哪个变量和哪些业务条件。",
        }], [], [])

    audit = audit_acceptance_contract(
        task.acceptance_contract,
        variables,
        task.execution_evidence,
        workspace_root=storage.resolve_workspace_root(),
    )

    # 契约只审「声明的交付物本身合不合格」。这两类残缺在契约里没有对应条款，也无法有：
    # 翻页一次没生效、加工节点什么也没改，两者都表现为变量非空、行数正常、状态全绿。
    # 判据是变量之间的逐字比较，不花额外调用。
    nodes = flow.definition.get("nodes", [])
    silent = _find_incomplete_sweeps(nodes, variables) + _find_ineffective_transforms(nodes, variables)
    silent += _find_garbage_rows(task.acceptance_contract, variables)

    issues = list(audit["issues"]) + [i for i in silent if i.get("severity") != "warning"]
    warnings = list(audit["warnings"]) + [i for i in silent if i.get("severity") == "warning"]
    return _verdict(base, issues, warnings, audit["deliverables"])


def _find_garbage_rows(contract: Any, variables: dict[str, Any]) -> list[dict[str, Any]]:
    """契约管「行里的字段对不对」，管不到「行本身是不是垃圾」。

    表头被当成数据行、半空行、整表被摊平成一个扁平数组、分页按钮混进结果——字段齐、
    行数够、日期合法、枚举合法，四条契约条款全过，交上来的还是一堆没法用的行。
    """
    findings: list[dict[str, Any]] = []
    for deliverable in contract.deliverables:
        raw = variables.get(deliverable.variable)
        # 非数组由契约的 deliverable_not_table 负责，这里再报一次只是重复。
        if deliverable.kind != "table" or not isinstance(raw, list):
            continue
        # 契约显式声明允许空表时，「空」不是缺陷；行数归契约管，这里只判形状。
        if not raw and resolved_min_rows(deliverable, variables) == 0:
            continue
        finding = _check_structured_rows(_coerce_table_rows(raw) or raw)
        if finding is not None:
            findings.append({**finding, "deliverable_id": deliverable.id})
    return findings


def _verdict(
    base: dict[str, Any],
    issues: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    deliverables: list[dict[str, Any]],
) -> dict[str, Any]:
    passed = not issues
    return {
        **base,
        "passed": passed,
        "deliverables": deliverables,
        "issues": issues,
        "warnings": warnings,
        "repair_plan": (
            _build_quality_repair_plan(issues)
            or ([{
                "action": "satisfy_acceptance_contract",
                "reason": "运行产物没有满足流程创建时冻结的交付条件。",
                "steps": [
                    "按 issues 定位交付变量、字段或业务约束的失败点。",
                    "只修流程节点，不得放宽验收契约；修好后重新运行当前 revision。",
                ],
            }] if issues else [])
        ),
        "message": (
            "运行产物满足验收契约。" if passed
            else "运行产物未通过验收，必须按 issues 修流程后重跑；这次运行不能作为交付依据。"
        ),
    }
