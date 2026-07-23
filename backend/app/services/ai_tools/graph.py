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
