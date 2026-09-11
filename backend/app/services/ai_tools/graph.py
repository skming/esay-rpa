"""流程图遍历原语：可达性、上下游收集。

lint 规则与执行器都按边关系做 BFS，抽出来避免两边各写一份遍历。
"""
from __future__ import annotations

from typing import Any

def _unreachable_node_ids(nodes: list[Any], edges: list[Any]) -> list[str]:
    """Unreachable node ids from entry (id/type "start", else first node) — must match executor's _select_run_start_node_id."""
    node_ids = [n.get("id") for n in nodes if isinstance(n, dict) and n.get("id")]
    if not node_ids:
        return []
    entry = next(
        (n.get("id") for n in nodes
         if isinstance(n, dict) and (n.get("id") == "start" or n.get("type") == "start")),
        node_ids[0],
    )
    adjacency: dict[str, list[str]] = {}
    for e in edges:
        if isinstance(e, dict) and e.get("source") and e.get("target"):
            adjacency.setdefault(e["source"], []).append(e["target"])
    reachable: set[str] = set()
    stack = [entry]
    while stack:
        cur = stack.pop()
        if cur in reachable:
            continue
        reachable.add(cur)
        stack.extend(t for t in adjacency.get(cur, []) if t not in reachable)
    return sorted(nid for nid in node_ids if nid not in reachable)

def _collect_ancestor_node_ids(node_id: str, parents_by_target: dict[str, list[str]]) -> set[str]:
    ancestors: set[str] = set()
    stack = list(parents_by_target.get(node_id, []))
    while stack:
        current = stack.pop()
        if current in ancestors:
            continue
        ancestors.add(current)
        stack.extend(parents_by_target.get(current, []))
    return ancestors

def _collect_downstream_nodes(
    node_id: str,
    downstream_by_source: dict[str, list[str]],
    node_map: dict[str, dict[str, Any]],
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """BFS 收集 node_id 的全部下游节点；limit 为 None 时不设上限。

    起点放进 seen，环形连线不会把起点自身算成下游。
    """
    collected: list[dict[str, Any]] = []
    seen = {node_id}
    queue = list(downstream_by_source.get(node_id, []))
    while queue and (limit is None or len(collected) < limit):
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        node = node_map.get(current)
        if node is not None:
            collected.append(node)
        queue.extend(downstream_by_source.get(current, []))
    return collected


def _find_swallowed_fork_targets(
    source_id: str,
    targets: list[str],
    downstream_by_source: dict[str, list[str]],
) -> list[tuple[str, str]]:
    """找出「下游被另一条分叉路径吞掉」的目标，返回 (被吞掉的目标, 吞掉它的兄弟)。

    task_manager 的遍历是单条 DFS：节点出栈才记 visited（task_manager.py:579-582），
    两条分叉谁先把下游走完由 `_push_edge_targets` 的倒序压栈决定。走在后面的那条分叉，
    其目标若已在这条链路上被走过，就命中 `node_id in visited` 被静默跳过——那条路径一次都不跑，
    流程照样报成功。这就是「回读校验挂在分叉另一侧、又汇合回抽取节点」丢校验步骤的原因。

    `_unreachable_node_ids` 看得见孤儿节点，看不见这件事：两条分叉从起点都可达。
    """
    found: list[tuple[str, str]] = []
    for index, target in enumerate(targets):
        for other_index, sibling in enumerate(targets):
            if index == other_index or target == sibling:
                continue
            if _reaches_from(sibling, target, downstream_by_source):
                found.append((target, sibling))
    return found


def _reaches_from(start: str, target: str, downstream_by_source: dict[str, list[str]]) -> bool:
    """target 是否落在 start 的下游。不含 start 自身，否则自环会被当成吞并。"""
    seen: set[str] = set()
    stack = list(downstream_by_source.get(start, []))
    while stack:
        current = stack.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(downstream_by_source.get(current, []))
    return False
