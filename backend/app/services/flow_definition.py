from __future__ import annotations

from app.models.schemas import RunScope, RunTaskRequest
from app.services.browser_action_runner import is_browser_action_node
from app.services.control_action_runner import is_control_action_node, is_human_takeover_node, is_subprocess_node
from app.services.data_action_runner import is_data_action_node
from app.services.file_action_runner import is_file_action_node
from app.services.flow_control import is_condition_node
from app.services.flow_loop import is_loop_node, is_repeat_until_node
from app.services.http_action_runner import is_http_action_node
from app.services.script_action_runner import is_script_action_node
from app.services.variable_action_runner import is_variable_action_node

type FlowNode = dict[str, object]
type FlowEdge = dict[str, object]


class FlowDefinitionSelector:
    """Inspects a flow definition dict to locate the primary fetch/browser node and build run requests."""

    @classmethod
    def select_executable_fetch_node(
        cls,
        definition: dict[str, object] | None,
        *,
        scope: RunScope = "full",
        start_node_id: str | None = None,
    ) -> FlowNode | None:
        nodes = cls.select_executable_fetch_nodes(definition, scope=scope, start_node_id=start_node_id)
        return nodes[0] if nodes else None

    @classmethod
    def select_executable_fetch_nodes(
        cls,
        definition: dict[str, object] | None,
        *,
        scope: RunScope = "full",
        start_node_id: str | None = None,
    ) -> list[FlowNode]:
        return [node for node in cls.select_executable_nodes(definition, scope=scope, start_node_id=start_node_id) if node.get("type") == "browser.fetch"]

    @classmethod
    def select_executable_nodes(
        cls,
        definition: dict[str, object] | None,
        *,
        scope: RunScope = "full",
        start_node_id: str | None = None,
    ) -> list[FlowNode]:
        if definition is None:
            return []

        # scope 三种取值：selected-only 只跑单个节点；from-selection 从指定节点开始按边遍历；
        # 其余情况（含 full）从流程的 start 节点遍历，遍历不到再退化为声明顺序全量执行。
        nodes = cls._enabled_nodes(definition)
        if start_node_id is not None and scope == "selected-only":
            nodes = [node for node in nodes if node.get("id") == start_node_id]
        elif start_node_id is not None and scope == "from-selection":
            scoped_nodes = cls._traverse_nodes(definition, nodes, start_node_id=start_node_id)
            nodes = scoped_nodes or nodes
        else:
            start_id = cls._find_start_node_id(nodes)
            scoped_nodes = cls._traverse_nodes(definition, nodes, start_node_id=start_id) if start_id is not None else []
            nodes = scoped_nodes or nodes
        return [node for node in nodes if is_executable_node(node)]

    @classmethod
    def build_request_for_fetch_node(cls, request: RunTaskRequest, node: FlowNode) -> RunTaskRequest:
        payload = request.model_dump(mode="json", by_alias=True)
        payload.update(
            {
                "targetUrl": cls.read_string(node, "targetUrl"),
                "selector": cls.read_string(node, "selector"),
                "fetcher": cls.read_string(node, "fetcher", default="static"),
                "extractMode": cls.read_string(node, "extractMode", default="text"),
                "attribute": cls.read_optional_string(node, "attribute"),
                "adaptive": cls.read_bool(node, "adaptive", default=True),
                "autoSave": cls.read_bool(node, "autoSave", default=True),
                "timeoutMs": cls.read_int(node, "timeoutMs", default=request.timeout_ms),
            }
        )
        return RunTaskRequest.model_validate(payload)

    @classmethod
    def ordered_nodes(cls, definition: dict[str, object]) -> list[FlowNode]:
        normalized_nodes = cls._enabled_nodes(definition)
        node_by_id = {node["id"]: node for node in normalized_nodes if isinstance(node.get("id"), str)}
        if not node_by_id:
            return normalized_nodes

        adjacency = cls._build_adjacency(definition, node_by_id)
        start_id = cls._find_start_node_id(normalized_nodes)
        if start_id is None or start_id not in node_by_id or not adjacency:
            return normalized_nodes

        ordered, visited = cls._walk_adjacency(start_id=start_id, node_by_id=node_by_id, adjacency=adjacency)
        ordered.extend(node for node in normalized_nodes if isinstance(node.get("id"), str) and node["id"] not in visited)
        ordered.extend(node for node in normalized_nodes if not isinstance(node.get("id"), str))
        return ordered

    @staticmethod
    def read_string(node: dict[str, object], key: str, *, default: str | None = None) -> str:
        value = node.get(key, default)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"browser.fetch 节点缺少 {key}")
        return value.strip()

    @staticmethod
    def read_optional_string(node: dict[str, object], key: str) -> str | None:
        value = node.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def read_bool(node: dict[str, object], key: str, *, default: bool) -> bool:
        value = node.get(key)
        return value if isinstance(value, bool) else default

    @staticmethod
    def read_int(node: dict[str, object], key: str, *, default: int) -> int:
        value = node.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else default

    @classmethod
    def _enabled_nodes(cls, definition: dict[str, object]) -> list[FlowNode]:
        nodes = definition.get("nodes")
        if not isinstance(nodes, list):
            return []
        return [node for node in nodes if isinstance(node, dict) and node.get("disabled") is not True]

    @classmethod
    def _traverse_nodes(cls, definition: dict[str, object], nodes: list[FlowNode], *, start_node_id: str) -> list[FlowNode]:
        enabled_ids = {node["id"] for node in nodes if isinstance(node.get("id"), str)}
        node_by_id = {node["id"]: node for node in cls._all_nodes(definition) if isinstance(node.get("id"), str)}
        if start_node_id not in node_by_id:
            return []
        adjacency = cls._build_adjacency(definition, node_by_id)
        if not adjacency:
            node = node_by_id[start_node_id]
            return [node] if node.get("id") in enabled_ids else []
        ordered, _visited = cls._walk_adjacency(start_id=start_node_id, node_by_id=node_by_id, adjacency=adjacency)
        return [node for node in ordered if node.get("id") in enabled_ids]

    @staticmethod
    def _all_nodes(definition: dict[str, object]) -> list[FlowNode]:
        nodes = definition.get("nodes")
        if not isinstance(nodes, list):
            return []
        return [node for node in nodes if isinstance(node, dict)]

    @staticmethod
    def _build_adjacency(definition: dict[str, object], node_by_id: dict[str, FlowNode]) -> dict[str, list[str]]:
        adjacency: dict[str, list[str]] = {}
        for source, edges in FlowDefinitionSelector.build_edge_adjacency(definition, node_by_id).items():
            targets = [_read_edge_target(edge) for edge in edges]
            adjacency[source] = [target for target in targets if target is not None]
        return adjacency

    @staticmethod
    def build_edge_adjacency(definition: dict[str, object], node_by_id: dict[str, FlowNode]) -> dict[str, list[FlowEdge]]:
        edges = definition.get("edges")
        adjacency: dict[str, list[FlowEdge]] = {}
        if not isinstance(edges, list):
            return adjacency

        for edge in edges:
            if not isinstance(edge, dict):
                continue
            source = edge.get("source")
            target = edge.get("target")
            if isinstance(source, str) and isinstance(target, str) and source in node_by_id and target in node_by_id:
                adjacency.setdefault(source, []).append(edge)
        return adjacency

    @staticmethod
    def _walk_adjacency(
        *,
        start_id: str,
        node_by_id: dict[str, FlowNode],
        adjacency: dict[str, list[str]],
    ) -> tuple[list[FlowNode], set[str]]:
        ordered: list[FlowNode] = []
        visited: set[str] = set()
        stack = [start_id]

        while stack:
            node_id = stack.pop()
            if node_id in visited:
                continue
            visited.add(node_id)
            node = node_by_id.get(node_id)
            if node is not None:
                ordered.append(node)
            for target_id in reversed(adjacency.get(node_id, [])):
                if target_id not in visited:
                    stack.append(target_id)

        return ordered, visited

    @staticmethod
    def _find_start_node_id(nodes: list[FlowNode]) -> str | None:
        for node in nodes:
            node_id = node.get("id")
            if node_id == "start" or node.get("type") == "start":
                return node_id if isinstance(node_id, str) else None
        first_id = nodes[0].get("id") if nodes else None
        return first_id if isinstance(first_id, str) else None


# 判据一律转发给各执行器自己的谓词，不在这里另抄一份类型清单：抄漏一个类型不会报错，
# 只会让该节点悄悄从 executable_nodes 里消失——进度条少算一步，且 headed 浏览器的
# 判定（扫描 executable_nodes 找 control.human_takeover）会永远扫不到，人工接管无从操作。
_NODE_PREDICATES = (
    is_variable_action_node,
    is_http_action_node,
    is_script_action_node,
    is_data_action_node,
    is_browser_action_node,
    is_control_action_node,
    is_subprocess_node,
    is_human_takeover_node,
    is_file_action_node,
    is_loop_node,
    is_repeat_until_node,
    is_condition_node,
)


def is_executable_node(node: FlowNode) -> bool:
    return node.get("type") == "browser.fetch" or any(predicate(node) for predicate in _NODE_PREDICATES)


def _read_edge_target(edge: FlowEdge) -> str | None:
    target = edge.get("target")
    return target if isinstance(target, str) else None
