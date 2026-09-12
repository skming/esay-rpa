from __future__ import annotations

import json
import re
from typing import Any

from app.models.schemas import CodeGenerateRequest, GeneratedScript
from app.services.flow_control import is_condition_node, read_condition_expression, select_branch_edges
from app.services.flow_loop import (
    is_loop_node,
    is_repeat_until_node,
    read_repeat_until_expression,
    split_loop_edges,
)

_INDENT = "    "
# 凭据不写进脚本，改由环境变量注入。前缀是为了在用户的 shell 里与其它变量区分开。
_CREDENTIAL_ENV_PREFIX = "RPA_"
_SENSITIVE_CATEGORIES = {"credential", "secret"}


class ScraplingCodeGenerator:
    # "导出为脚本"功能用：把流程定义转成独立可运行的 Scrapling Python 脚本，
    # 供用户脱离本平台直接执行；不是流程运行时使用的路径。
    def generate(self, request: CodeGenerateRequest) -> GeneratedScript:
        flow_name = request.flow_name.strip()
        filename = f"{self._slugify(flow_name)}.py"
        content = "\n".join(self._build_flow_script_lines(request))

        return GeneratedScript(
            filename=filename,
            dependencies=["scrapling[all]>=0.3.0"],
            content=content,
        )

    def _build_flow_script_lines(self, request: CodeGenerateRequest) -> list[str]:
        flow = request.flow_definition
        if flow is None:
            raise ValueError("flowDefinition 不能为空")

        variables, credentials = self._read_input_variables(flow)
        lines = [
            "from __future__ import annotations",
            "",
            "import json",
            "import os",
            "import re",
            "import time",
            "from pathlib import Path",
            "from typing import Any",
            "",
            "from scrapling.fetchers import DynamicFetcher, Fetcher, StealthyFetcher",
            "",
            "",
            f"FLOW_NAME = {json.dumps(request.flow_name, ensure_ascii=False)}",
            f"VARIABLES: dict[str, Any] = {json.dumps(variables, ensure_ascii=False, indent=2)}",
            f"CREDENTIAL_ENV: dict[str, str] = {json.dumps(credentials, ensure_ascii=False, indent=2)}",
            "",
            "",
        ]
        lines.extend(self._runtime_helper_lines())
        lines.extend(
            [
                "def run() -> dict[str, Any]:",
                f'{_INDENT}"""运行从 Easy RPA 生成的 {self._safe_doc(request.flow_name)} 全流程 Scrapling 脚本。"""',
                f"{_INDENT}variables = dict(VARIABLES)",
                f"{_INDENT}variables.update(load_credentials())",
                f"{_INDENT}page = None",
                f"{_INDENT}outputs: dict[str, Any] = {{}}",
            ]
        )

        body = self._emit_chain(self._build_graph(flow), "start", stop_at=None, depth=1, active=set())
        lines.extend(body or [f"{_INDENT}pass"])

        lines.extend(
            [
                f"{_INDENT}return {{'variables': variables, 'outputs': outputs}}",
                "",
                "",
                "if __name__ == '__main__':",
                f"{_INDENT}result = run()",
                f"{_INDENT}print(json.dumps(result, ensure_ascii=False, indent=2))",
                "",
            ]
        )
        return lines

    # ---------- 图遍历 ----------

    def _build_graph(self, flow: dict[str, object]) -> dict[str, Any]:
        raw_nodes = flow.get("nodes")
        nodes = [node for node in raw_nodes if isinstance(node, dict)] if isinstance(raw_nodes, list) else []
        by_id = {str(node.get("id")): node for node in nodes if node.get("id") is not None}

        adjacency: dict[str, list[dict[str, Any]]] = {}
        raw_edges = flow.get("edges")
        if isinstance(raw_edges, list):
            for edge in raw_edges:
                if not isinstance(edge, dict):
                    continue
                source, target = edge.get("source"), edge.get("target")
                if isinstance(source, str) and isinstance(target, str):
                    adjacency.setdefault(source, []).append(edge)

        start_id = "start" if "start" in by_id else next(iter(by_id), "")
        return {"by_id": by_id, "adjacency": adjacency, "start": start_id, "order": [str(n.get("id")) for n in nodes]}

    def _emit_chain(
        self, graph: dict[str, Any], node_id: str | None, *, stop_at: str | None, depth: int, active: set[str]
    ) -> list[str]:
        """沿一条链发射代码，遇到分支/循环节点转交给对应的发射器。

        `stop_at` 是汇合点：分支的两条腿都发射到汇合点为止，汇合点本身由外层发射一次，
        否则 if/else 之后的公共链路会被写两遍。`active` 防住图里的环把递归拖进死循环。
        """
        lines: list[str] = []
        current = node_id
        while current and current != stop_at:
            if current in active:
                # 回边（循环体末尾指回循环节点）：链在此自然结束，不是错误
                break
            node = graph["by_id"].get(current)
            if node is None:
                break
            active = active | {current}

            if is_condition_node(node):
                lines.extend(self._emit_condition(graph, node, depth=depth, active=active))
                current = self._join_point(graph, node)
                continue
            if is_loop_node(node) or is_repeat_until_node(node):
                emitted, current = self._emit_loop(graph, node, depth=depth, active=active)
                lines.extend(emitted)
                continue

            lines.extend(self._node_lines(node, depth))
            outgoing = graph["adjacency"].get(current, [])
            current = str(outgoing[0].get("target")) if outgoing else None
        return lines

    def _emit_condition(
        self, graph: dict[str, Any], node: dict[str, Any], *, depth: int, active: set[str]
    ) -> list[str]:
        node_id = str(node.get("id"))
        expression = read_condition_expression(node) or ""
        outgoing = graph["adjacency"].get(node_id, [])
        true_edges = select_branch_edges(outgoing, True)
        false_edges = select_branch_edges(outgoing, False)
        join = self._join_point(graph, node)
        pad = _INDENT * depth

        lines = [f"{pad}# {self._safe_comment(str(node.get('title') or node_id))} · {self._safe_comment(str(node.get('type')))}"]
        lines.append(f"{pad}if evaluate_condition({json.dumps(expression, ensure_ascii=False)}, variables):")
        true_body = self._branch_body(graph, true_edges, stop_at=join, depth=depth + 1, active=active)
        lines.extend(true_body or [f"{pad}{_INDENT}pass"])
        lines.append(f"{pad}else:")
        false_body = self._branch_body(graph, false_edges, stop_at=join, depth=depth + 1, active=active)
        lines.extend(false_body or [f"{pad}{_INDENT}pass"])
        return lines

    def _branch_body(
        self, graph: dict[str, Any], edges: list[dict[str, Any]], *, stop_at: str | None, depth: int, active: set[str]
    ) -> list[str]:
        lines: list[str] = []
        for edge in edges:
            target = edge.get("target")
            if isinstance(target, str):
                lines.extend(self._emit_chain(graph, target, stop_at=stop_at, depth=depth, active=active))
        return lines

    def _emit_loop(
        self, graph: dict[str, Any], node: dict[str, Any], *, depth: int, active: set[str]
    ) -> tuple[list[str], str | None]:
        node_id = str(node.get("id"))
        outgoing = graph["adjacency"].get(node_id, [])
        body_edges, exit_edges = split_loop_edges(outgoing, loop_node_id=node_id, adjacency=graph["adjacency"])
        pad = _INDENT * depth
        title = self._safe_comment(str(node.get("title") or node_id))
        lines = [f"{pad}# {title} · {self._safe_comment(str(node.get('type')))}"]

        if is_loop_node(node):
            items = self._first_string(node, ("itemsVariable", "listVariable", "inputVariable"))
            if not items:
                raise ValueError(f"循环节点 {node_id} 缺少 itemsVariable，无法导出为脚本")
            item_var = self._first_string(node, ("itemVariable",)) or "current_item"
            index_var = self._first_string(node, ("indexVariable",)) or "loop_index"
            max_iterations = self._read_max_iterations(node, default=1000)
            lines.append(f"{pad}for {index_var}, item in enumerate(loop_items(variables, {json.dumps(items)})[:{max_iterations}]):")
            lines.append(f"{pad}{_INDENT}variables[{json.dumps(item_var)}] = item")
            lines.append(f"{pad}{_INDENT}variables[{json.dumps(index_var)}] = {index_var}")
        else:
            expression = read_repeat_until_expression(node)
            max_iterations = self._read_max_iterations(node, default=50)
            index_var = self._first_string(node, ("indexVariable",)) or "repeat_index"
            # 跑满上限说明退出条件始终没满足，与运行时一致：默认必须失败，静默退出会让
            # 「翻月没翻到位」这种错误状态带着错数据继续跑完。
            fail_on_max = node.get("continueOnMaxIterations") is not True
            lines.append(f"{pad}{index_var} = 0")
            lines.append(f"{pad}while not evaluate_condition({json.dumps(expression, ensure_ascii=False)}, variables):")
            lines.append(f"{pad}{_INDENT}if {index_var} >= {max_iterations}:")
            if fail_on_max:
                lines.append(
                    f"{pad}{_INDENT}{_INDENT}raise RuntimeError("
                    f"{json.dumps(f'repeat_until 跑满 {max_iterations} 轮，退出条件始终未满足', ensure_ascii=False)})"
                )
            else:
                lines.append(f"{pad}{_INDENT}{_INDENT}break")
            lines.append(f"{pad}{_INDENT}{index_var} += 1")
            lines.append(f"{pad}{_INDENT}variables[{json.dumps(index_var)}] = {index_var}")

        body = self._branch_body(graph, body_edges, stop_at=node_id, depth=depth + 1, active=active)
        lines.extend(body or [f"{pad}{_INDENT}pass"])

        exit_target = next((str(e.get("target")) for e in exit_edges if isinstance(e.get("target"), str)), None)
        return lines, exit_target

    def _join_point(self, graph: dict[str, Any], node: dict[str, Any]) -> str | None:
        """两条分支最早的汇合节点；没有汇合点返回 None。"""
        node_id = str(node.get("id"))
        outgoing = graph["adjacency"].get(node_id, [])
        branch_starts = [str(e.get("target")) for e in outgoing if isinstance(e.get("target"), str)]
        if len(branch_starts) < 2:
            return None

        reach_sets = [self._reachable(graph, start) for start in branch_starts]
        common = set.intersection(*reach_sets) if reach_sets else set()
        if not common:
            return None
        # 取拓扑上最早的那个：按第一条分支的遍历顺序找首个公共节点
        for candidate in self._reachable_in_order(graph, branch_starts[0]):
            if candidate in common:
                return candidate
        return None

    def _reachable(self, graph: dict[str, Any], start: str) -> set[str]:
        return set(self._reachable_in_order(graph, start))

    def _reachable_in_order(self, graph: dict[str, Any], start: str) -> list[str]:
        order: list[str] = []
        seen: set[str] = set()
        stack = [start]
        while stack:
            current = stack.pop(0)
            if current in seen or current not in graph["by_id"]:
                continue
            seen.add(current)
            order.append(current)
            stack.extend(
                str(e.get("target")) for e in graph["adjacency"].get(current, []) if isinstance(e.get("target"), str)
            )
        return order

    # ---------- 生成脚本自带的运行时 ----------

    def _runtime_helper_lines(self) -> list[str]:
        return [
            "_MISSING = object()",
            "",
            "",
            "def load_credentials() -> dict[str, str]:",
            f"{_INDENT}\"\"\"凭据从环境变量读，不写进脚本。\"\"\"",
            f"{_INDENT}resolved: dict[str, str] = {{}}",
            f"{_INDENT}missing: list[str] = []",
            f"{_INDENT}for name, env_key in CREDENTIAL_ENV.items():",
            f"{_INDENT}{_INDENT}value = os.environ.get(env_key)",
            f"{_INDENT}{_INDENT}if not value:",
            f"{_INDENT}{_INDENT}{_INDENT}missing.append(env_key)",
            f"{_INDENT}{_INDENT}else:",
            f"{_INDENT}{_INDENT}{_INDENT}resolved[name] = value",
            f"{_INDENT}if missing:",
            # 空密码不会让抓取报错，只会抓回登录页，所以必须在跑之前停住
            f"{_INDENT}{_INDENT}raise RuntimeError('缺少环境变量：' + ', '.join(missing))",
            f"{_INDENT}return resolved",
            "",
            "",
            "def render_template(value: Any, variables: dict[str, Any]) -> str:",
            f"{_INDENT}text = '' if value is None else str(value)",
            f"{_INDENT}return re.sub(r'\\$\\{{var\\.([^}}]+)\\}}', lambda match: str(variables.get(match.group(1), '')), text)",
            "",
            "",
            "def _read_nested_value(name: str, values: dict[str, Any]) -> Any:",
            f"{_INDENT}parts = name.split('.')",
            f"{_INDENT}for split_index in range(len(parts) - 1, 0, -1):",
            f"{_INDENT}{_INDENT}root_name = '.'.join(parts[:split_index])",
            f"{_INDENT}{_INDENT}if root_name not in values:",
            f"{_INDENT}{_INDENT}{_INDENT}continue",
            f"{_INDENT}{_INDENT}current = values[root_name]",
            f"{_INDENT}{_INDENT}for part in parts[split_index:]:",
            f"{_INDENT}{_INDENT}{_INDENT}if isinstance(current, dict):",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}if part not in current:",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}{_INDENT}return _MISSING",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}current = current[part]",
            f"{_INDENT}{_INDENT}{_INDENT}elif isinstance(current, list) and part.isdigit():",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}if int(part) >= len(current):",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}{_INDENT}return _MISSING",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}current = current[int(part)]",
            f"{_INDENT}{_INDENT}{_INDENT}else:",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}return _MISSING",
            f"{_INDENT}{_INDENT}return current",
            f"{_INDENT}return _MISSING",
            "",
            "",
            "def read_variable(variables: dict[str, Any], name: str) -> Any:",
            f"{_INDENT}normalized = name.strip()",
            f"{_INDENT}if normalized in variables:",
            f"{_INDENT}{_INDENT}return variables[normalized]",
            f"{_INDENT}nested = _read_nested_value(normalized, variables)",
            f"{_INDENT}if nested is _MISSING:",
            # 变量名写错时静默取 None 会让条件判成 False 后继续跑完并交出错数据
            f"{_INDENT}{_INDENT}raise ValueError('变量未定义: ' + normalized)",
            f"{_INDENT}return nested",
            "",
            "",
            "def materialize_loop_item(item: Any) -> Any:",
            f"{_INDENT}if not isinstance(item, str):",
            f"{_INDENT}{_INDENT}return item",
            f"{_INDENT}normalized = item.strip()",
            f"{_INDENT}if not normalized or normalized[0] not in '[{{':",
            f"{_INDENT}{_INDENT}return item",
            f"{_INDENT}try:",
            f"{_INDENT}{_INDENT}return json.loads(normalized)",
            f"{_INDENT}except json.JSONDecodeError:",
            f"{_INDENT}{_INDENT}return item",
            "",
            "",
            "def loop_items(variables: dict[str, Any], name: str) -> list[Any]:",
            f"{_INDENT}value = read_variable(variables, name)",
            f"{_INDENT}if isinstance(value, list):",
            f"{_INDENT}{_INDENT}return value",
            f"{_INDENT}if isinstance(value, dict):",
            f"{_INDENT}{_INDENT}return [{{'key': key, 'value': item}} for key, item in value.items()]",
            f"{_INDENT}if isinstance(value, str):",
            f"{_INDENT}{_INDENT}normalized = value.strip()",
            f"{_INDENT}{_INDENT}if not normalized:",
            f"{_INDENT}{_INDENT}{_INDENT}return []",
            f"{_INDENT}{_INDENT}try:",
            f"{_INDENT}{_INDENT}{_INDENT}decoded = json.loads(normalized)",
            f"{_INDENT}{_INDENT}except json.JSONDecodeError as exc:",
            f"{_INDENT}{_INDENT}{_INDENT}raise ValueError('循环变量必须是列表或 JSON 数组: ' + name) from exc",
            f"{_INDENT}{_INDENT}return loop_items({{name: decoded}}, name)",
            f"{_INDENT}raise ValueError('循环变量必须是列表: ' + name)",
            "",
            "",
        ] + self._condition_helper_lines() + [
            "def fetch_page(url: str, fetcher: str = 'static') -> Any:",
            f"{_INDENT}if fetcher == 'dynamic':",
            f"{_INDENT}{_INDENT}return DynamicFetcher.fetch(url, headless=True, network_idle=True)",
            f"{_INDENT}if fetcher == 'stealthy':",
            f"{_INDENT}{_INDENT}return StealthyFetcher.fetch(url, headless=True, network_idle=True)",
            f"{_INDENT}return Fetcher.get(url)",
            "",
            "",
            "def extract_values(page: Any, selector: str, mode: str = 'text', attribute: str | None = None) -> list[str]:",
            f"{_INDENT}if page is None:",
            f"{_INDENT}{_INDENT}raise RuntimeError('页面尚未打开，无法提取数据')",
            f"{_INDENT}if mode == 'attribute':",
            f"{_INDENT}{_INDENT}attr = attribute or 'href'",
            f"{_INDENT}{_INDENT}return [str(value) for value in page.css(f'{{selector}}::attr({{attr}})').getall()]",
            f"{_INDENT}if mode == 'html':",
            f"{_INDENT}{_INDENT}return [str(element.html_content) for element in page.css(selector)]",
            f"{_INDENT}if mode == 'count':",
            f"{_INDENT}{_INDENT}return [str(len(page.css(selector)))]",
            f"{_INDENT}if '::text' in selector:",
            f"{_INDENT}{_INDENT}return [str(value) for value in page.css(selector).getall()]",
            # Element.text 只取节点自身文本，<td><span>值</span></td> 会返回空串；
            # 与 scrapling_runner 保持一致走 get_all_text 遍历子孙。
            f"{_INDENT}return [str(element.get_all_text(strip=True)) for element in page.css(selector)]",
            "",
            "",
            "def save_output(variables: dict[str, Any], node: dict[str, Any], values: list[str], mode: str = 'text') -> None:",
            f"{_INDENT}output_name = node.get('outputVariable') or node.get('responseVariable')",
            f"{_INDENT}if output_name:",
            f"{_INDENT}{_INDENT}variables[str(output_name)] = values",
            f"{_INDENT}first_name = node.get('firstValueVariable')",
            f"{_INDENT}if first_name:",
            f"{_INDENT}{_INDENT}variables[str(first_name)] = values[0] if values else ''",
            f"{_INDENT}count_name = node.get('countVariable') or node.get('statusVariable')",
            f"{_INDENT}if count_name:",
            # count 模式的 values 是 ["7"] 这种长度恒为 1 的列表，len() 会把计数写成 1
            f"{_INDENT}{_INDENT}if mode == 'count':",
            f"{_INDENT}{_INDENT}{_INDENT}variables[str(count_name)] = int(values[0]) if values else 0",
            f"{_INDENT}{_INDENT}else:",
            f"{_INDENT}{_INDENT}{_INDENT}variables[str(count_name)] = len(values)",
            "",
            "",
        ]

    def _condition_helper_lines(self) -> list[str]:
        """条件求值的第二份实现。

        导出脚本必须独立运行，不能 import 平台代码，所以 flow_control 的语义只能在这里
        复制一份。两份一旦分叉，同一个流程在平台里和导出后会走不同分支且都不报错——
        test_generated_condition_evaluator_agrees_with_the_runtime_one 是唯一的约束，
        改动任一侧都必须让它保持绿。
        """
        return [
            "_COMPARISON_OPERATORS = ('==', '!=', '>=', '<=', '>', '<')",
            "_NUMERIC_PATTERN = re.compile(r'^-?(?:\\d+\\.?\\d*|\\.\\d+)$')",
            "_BARE_VARIABLE_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_.-]{0,119}$')",
            "_TRUE_WORDS = {'1', 'true', 'yes', 'y', '是', '真'}",
            "_FALSE_WORDS = {'', '0', 'false', 'no', 'n', '否', '假'}",
            "_MAX_EXPRESSION_LENGTH = 500",
            "",
            "",
            "def _find_operator(expression: str) -> tuple[str, str, str] | None:",
            f"{_INDENT}quote = None",
            f"{_INDENT}escaped = False",
            f"{_INDENT}for index, char in enumerate(expression):",
            f"{_INDENT}{_INDENT}if escaped:",
            f"{_INDENT}{_INDENT}{_INDENT}escaped = False",
            f"{_INDENT}{_INDENT}{_INDENT}continue",
            f"{_INDENT}{_INDENT}if char == '\\\\' and quote is not None:",
            f"{_INDENT}{_INDENT}{_INDENT}escaped = True",
            f"{_INDENT}{_INDENT}{_INDENT}continue",
            f"{_INDENT}{_INDENT}if char in {{chr(39), chr(34)}}:",
            f"{_INDENT}{_INDENT}{_INDENT}quote = None if quote == char else (char if quote is None else quote)",
            f"{_INDENT}{_INDENT}{_INDENT}continue",
            f"{_INDENT}{_INDENT}if quote is not None:",
            f"{_INDENT}{_INDENT}{_INDENT}continue",
            f"{_INDENT}{_INDENT}for operator in _COMPARISON_OPERATORS:",
            f"{_INDENT}{_INDENT}{_INDENT}if expression.startswith(operator, index):",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}left = expression[:index].strip()",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}right = expression[index + len(operator):].strip()",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}if not left or not right:",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}{_INDENT}raise ValueError('条件表达式缺少比较值: ' + expression)",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}return left, operator, right",
            f"{_INDENT}return None",
            "",
            "",
            "def _parse_operand(value: str, variables: dict[str, Any]) -> Any:",
            f"{_INDENT}value = value.strip()",
            f"{_INDENT}if not value:",
            f"{_INDENT}{_INDENT}raise ValueError('条件表达式包含空值')",
            f"{_INDENT}if (value.startswith(chr(34)) and value.endswith(chr(34))) or (value.startswith(chr(39)) and value.endswith(chr(39))):",
            # 与 flow_control 一致地还原转义，否则 'a\\'b' 两边比较结果不同
            f"{_INDENT}{_INDENT}inner = value[1:-1]",
            f"{_INDENT}{_INDENT}return inner.replace(chr(92) + chr(34), chr(34)).replace(chr(92) + chr(39), chr(39)).replace(chr(92) + chr(92), chr(92))",
            f"{_INDENT}lowered = value.lower()",
            f"{_INDENT}if lowered == 'true':",
            f"{_INDENT}{_INDENT}return True",
            f"{_INDENT}if lowered == 'false':",
            f"{_INDENT}{_INDENT}return False",
            f"{_INDENT}if lowered in {{'null', 'none'}}:",
            f"{_INDENT}{_INDENT}return None",
            f"{_INDENT}if _NUMERIC_PATTERN.match(value):",
            f"{_INDENT}{_INDENT}return float(value) if '.' in value else int(value)",
            f"{_INDENT}if _BARE_VARIABLE_PATTERN.match(value):",
            f"{_INDENT}{_INDENT}return read_variable(variables, value)",
            f"{_INDENT}raise ValueError('不支持的条件表达式值: ' + value)",
            "",
            "",
            "def _to_number(value: Any) -> float | None:",
            f"{_INDENT}if isinstance(value, bool):",
            f"{_INDENT}{_INDENT}return None",
            f"{_INDENT}if isinstance(value, (int, float)):",
            f"{_INDENT}{_INDENT}return float(value)",
            f"{_INDENT}if isinstance(value, str) and _NUMERIC_PATTERN.match(value.strip()):",
            f"{_INDENT}{_INDENT}return float(value.strip())",
            f"{_INDENT}return None",
            "",
            "",
            "def _normalize_equality(value: Any) -> Any:",
            f"{_INDENT}if isinstance(value, str):",
            f"{_INDENT}{_INDENT}lowered = value.strip().lower()",
            f"{_INDENT}{_INDENT}if lowered == 'true':",
            f"{_INDENT}{_INDENT}{_INDENT}return True",
            f"{_INDENT}{_INDENT}if lowered == 'false':",
            f"{_INDENT}{_INDENT}{_INDENT}return False",
            f"{_INDENT}{_INDENT}number = _to_number(value)",
            f"{_INDENT}{_INDENT}return number if number is not None else value",
            f"{_INDENT}return value",
            "",
            "",
            "def _to_bool(value: Any) -> bool:",
            f"{_INDENT}if isinstance(value, bool):",
            f"{_INDENT}{_INDENT}return value",
            f"{_INDENT}if isinstance(value, (int, float)):",
            f"{_INDENT}{_INDENT}return value != 0",
            f"{_INDENT}if isinstance(value, str):",
            f"{_INDENT}{_INDENT}normalized = value.strip().lower()",
            f"{_INDENT}{_INDENT}if normalized in _FALSE_WORDS:",
            f"{_INDENT}{_INDENT}{_INDENT}return False",
            f"{_INDENT}{_INDENT}if normalized in _TRUE_WORDS:",
            f"{_INDENT}{_INDENT}{_INDENT}return True",
            f"{_INDENT}return value is not None",
            "",
            "",
            "def evaluate_condition(expression: str, variables: dict[str, Any]) -> bool:",
            f"{_INDENT}if len(expression) > _MAX_EXPRESSION_LENGTH:",
            f"{_INDENT}{_INDENT}raise ValueError('条件表达式不能超过 500 个字符')",
            f"{_INDENT}found = _find_operator(expression)",
            f"{_INDENT}if found is None:",
            f"{_INDENT}{_INDENT}return _to_bool(_parse_operand(expression, variables))",
            f"{_INDENT}left_text, operator, right_text = found",
            f"{_INDENT}left = _parse_operand(left_text, variables)",
            f"{_INDENT}right = _parse_operand(right_text, variables)",
            f"{_INDENT}left_number = _to_number(left)",
            f"{_INDENT}right_number = _to_number(right)",
            f"{_INDENT}if operator in {{'>', '>=', '<', '<='}}:",
            f"{_INDENT}{_INDENT}if left_number is None or right_number is None:",
            f"{_INDENT}{_INDENT}{_INDENT}raise ValueError('大小比较仅支持数字变量和值')",
            f"{_INDENT}{_INDENT}if operator == '>':",
            f"{_INDENT}{_INDENT}{_INDENT}return left_number > right_number",
            f"{_INDENT}{_INDENT}if operator == '>=':",
            f"{_INDENT}{_INDENT}{_INDENT}return left_number >= right_number",
            f"{_INDENT}{_INDENT}if operator == '<':",
            f"{_INDENT}{_INDENT}{_INDENT}return left_number < right_number",
            f"{_INDENT}{_INDENT}return left_number <= right_number",
            f"{_INDENT}if left_number is not None and right_number is not None:",
            f"{_INDENT}{_INDENT}is_equal = left_number == right_number",
            f"{_INDENT}else:",
            f"{_INDENT}{_INDENT}is_equal = _normalize_equality(left) == _normalize_equality(right)",
            f"{_INDENT}return is_equal if operator == '==' else not is_equal",
            "",
            "",
        ]

    # ---------- 单节点 ----------

    def _node_lines(self, node: dict[str, Any], depth: int) -> list[str]:
        pad = _INDENT * depth
        node_type = str(node.get("type") or "")
        title = str(node.get("title") or node.get("id") or node_type)
        lines = [f"{pad}# {self._safe_comment(title)} · {self._safe_comment(node_type)}"]

        if node_type in {"start", "end"}:
            lines.append(f"{pad}pass")
            return lines

        if node.get("disabled") is True:
            lines.append(f"{pad}# 已禁用，跳过。")
            lines.append(f"{pad}pass")
            return lines

        if node_type == "browser.paginateNext":
            lines.extend(self._pagination_lines(node, depth))
            return lines

        if node_type in {"browser.open", "browser.tab.open", "browser.fetch"}:
            url_expr = self._node_string_expr(node.get("targetUrl") or node.get("url"))
            fetcher = str(node.get("fetcher") or "static")
            lines.append(f"{pad}page = fetch_page(render_template({url_expr}, variables), {json.dumps(fetcher)})")
            if node_type == "browser.fetch" and node.get("selector"):
                lines.extend(self._extract_lines(node, depth))
            return lines

        if node_type in {"browser.extract", "ui.extract"}:
            lines.extend(self._extract_lines(node, depth))
            return lines

        if node_type in {"control.delay"}:
            delay_ms = int(node.get("delayMs") or node.get("timeoutMs") or 1000)
            lines.append(f"{pad}time.sleep({max(delay_ms, 0) / 1000:.3f})")
            return lines

        if node_type == "variable.set":
            name = node.get("variableName") or node.get("outputVariable") or node.get("responseVariable")
            value = node.get("value") if node.get("value") is not None else node.get("defaultValue")
            if name:
                lines.append(
                    f"{pad}variables[{json.dumps(str(name))}] = render_template({self._node_string_expr(value)}, variables)"
                )
            else:
                lines.append(f"{pad}# 缺少 variableName，跳过变量写入。")
                lines.append(f"{pad}pass")
            return lines

        if node_type == "file.write":
            path = self._node_string_expr(node.get("path"))
            content = self._node_string_expr(node.get("content") or node.get("value"))
            lines.append(
                f"{pad}Path(render_template({path}, variables)).write_text("
                f"render_template({content}, variables), encoding='utf-8')"
            )
            return lines

        if node_type.startswith("browser.") or node_type.startswith("ui."):
            lines.append(f"{pad}# Scrapling 是采集引擎，不执行真实点击/输入；此交互节点已保留为注释。")
            lines.append(f"{pad}pass")
            return lines

        lines.append(f"{pad}# 当前节点类型暂未映射到 Scrapling 脚本，已保留为流程注释。")
        lines.append(f"{pad}pass")
        return lines

    def _pagination_lines(self, node: dict[str, Any], depth: int) -> list[str]:
        """点击式翻页在 Scrapling 里没有对应能力，只能拒绝。

        此前它落进 browser.* 兜底变成 pass：脚本只抓第 1 页，跑完还报成功。
        运行时对「只翻到第 1 页」有硬门控，导出路径不能比它松。
        """
        pad = _INDENT * depth
        url_template = str(node.get("urlTemplate") or "").strip()
        if not url_template:
            raise ValueError(
                f"翻页节点 {node.get('id') or ''} 是点击式，Scrapling 不执行点击，无法导出为脚本；"
                "改用 urlTemplate（形如 https://example.com/list?p=${page}）后可导出"
            )

        start_page = self._read_int(node, "startPage", default=1)
        page_step = self._read_int(node, "pageStep", default=1) or 1
        # 与 browser_action_runner._paginate_next_and_extract 同名同默认值；
        # maxPages 是 FIELD_ALIAS_HINTS 里的错写法，平台任何一层都不读。
        max_pages = self._read_int(node, "maxIterations", default=20)
        selector = self._node_string_expr(node.get("targetSelector") or node.get("selector"))
        mode = str(node.get("extractMode") or "text")
        payload = json.dumps(self._output_node_payload(node), ensure_ascii=False)
        stop = start_page + page_step * max_pages
        return [
            f"{pad}collected: list[str] = []",
            f"{pad}for page_number in range({start_page}, {stop}, {page_step}):",
            f"{pad}{_INDENT}page = fetch_page(render_template("
            f"{json.dumps(url_template, ensure_ascii=False)}.replace('${{page}}', str(page_number)), variables))",
            f"{pad}{_INDENT}page_values = extract_values(page, render_template({selector}, variables), {json.dumps(mode)})",
            # 翻到空页即停：URL 式翻页越界通常返回空列表而不是 404
            f"{pad}{_INDENT}if not page_values:",
            f"{pad}{_INDENT}{_INDENT}break",
            f"{pad}{_INDENT}collected.extend(page_values)",
            f"{pad}node = {payload}",
            f"{pad}save_output(variables, node, collected, {json.dumps(mode)})",
            f"{pad}outputs[str(node.get('id') or node.get('title') or 'paginate')] = collected",
        ]

    def _extract_lines(self, node: dict[str, Any], depth: int) -> list[str]:
        pad = _INDENT * depth
        selector = self._node_string_expr(node.get("selector"))
        mode = str(node.get("extractMode") or "text")
        attribute = node.get("attribute")
        return [
            f"{pad}values = extract_values(page, render_template({selector}, variables), "
            f"{json.dumps(mode)}, {json.dumps(attribute)})",
            f"{pad}if not values and not {json.dumps(bool(node.get('continueOnError')))}:",
            f"{pad}{_INDENT}raise RuntimeError("
            f"{json.dumps('未找到目标元素: ' + str(node.get('selector') or ''), ensure_ascii=False)})",
            f"{pad}node = {json.dumps(self._output_node_payload(node), ensure_ascii=False)}",
            f"{pad}save_output(variables, node, values, {json.dumps(mode)})",
            f"{pad}outputs[str(node.get('id') or node.get('title') or 'extract')] = values",
        ]

    # ---------- 输入变量 ----------

    def _read_input_variables(self, flow: dict[str, object]) -> tuple[dict[str, Any], dict[str, str]]:
        """返回（可内联的变量, 凭据名→环境变量名）。

        凭据值一律不写进脚本：导出文件会被下载、提交进仓库、转发给同事，而平台其它
        出口（见 ai_flow_state._render_variables）都只报「已填/未填」。
        """
        raw_variables = flow.get("inputVariables")
        if not isinstance(raw_variables, list):
            return {}, {}

        variables: dict[str, Any] = {}
        credentials: dict[str, str] = {}
        used_env_keys: set[str] = set()
        for item in raw_variables:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            name = name.strip()
            if self._is_sensitive(item):
                credentials[name] = self._env_key(name, used_env_keys)
                continue
            variables[name] = self._parse_variable_value(str(item.get("value") or ""), str(item.get("type") or "String"))
        return variables, credentials

    def _is_sensitive(self, item: dict[str, Any]) -> bool:
        return item.get("sensitive") is True or str(item.get("category") or "").lower() in _SENSITIVE_CATEGORIES

    def _env_key(self, name: str, used: set[str]) -> str:
        """变量名转环境变量名。大写归一后可能撞车（api-key 与 api_key），加序号区分。"""
        base = _CREDENTIAL_ENV_PREFIX + re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
        if not base.strip("_"):
            base = _CREDENTIAL_ENV_PREFIX + "SECRET"
        key = base
        suffix = 2
        while key in used:
            key = f"{base}_{suffix}"
            suffix += 1
        used.add(key)
        return key

    def _parse_variable_value(self, value: str, value_type: str) -> Any:
        if value_type == "Integer":
            try:
                return int(value)
            except ValueError:
                return 0
        if value_type == "Boolean":
            return value.strip().lower() in {"true", "1", "yes", "y", "是"}
        if value_type in {"List", "Dict"}:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return [] if value_type == "List" else {}
        return value

    # ---------- 杂项 ----------

    def _first_string(self, node: dict[str, Any], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _read_int(self, node: dict[str, Any], key: str, *, default: int) -> int:
        value = node.get(key)
        if isinstance(value, bool) or value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _read_max_iterations(self, node: dict[str, Any], *, default: int) -> int:
        """与 flow_loop._read_max_iterations 同规则：非法值当场报错、上限 10000。

        导出时按同样的规则取值，否则同一个流程在平台里拒绝执行、导出后却能跑。
        """
        raw = node.get("maxIterations", node.get("limit", default))
        if isinstance(raw, bool):
            raise ValueError("循环 maxIterations 必须是正整数")
        if isinstance(raw, int):
            value = raw
        elif isinstance(raw, str) and raw.strip().isdigit():
            value = int(raw.strip())
        else:
            value = default
        if value < 1:
            raise ValueError("循环 maxIterations 必须大于 0")
        return min(value, 10_000)

    def _output_node_payload(self, node: dict[str, Any]) -> dict[str, Any]:
        keys = ("id", "title", "outputVariable", "responseVariable", "firstValueVariable", "countVariable", "statusVariable")
        return {key: node[key] for key in keys if key in node and node[key] is not None}

    def _node_string_expr(self, value: object) -> str:
        return json.dumps("" if value is None else str(value), ensure_ascii=False)

    def _slugify(self, value: str) -> str:
        # 只保留 ASCII 会让所有中文流程名塌成同一个兜底名，导出第二个流程直接覆盖第一个。
        slug = re.sub(r"[^\w]+", "-", value, flags=re.UNICODE).strip("-").lower()
        return slug or "rpa-flow"

    def _safe_doc(self, value: str) -> str:
        return value.replace('"""', "").replace("\n", " ").strip() or "RPA 流程"

    def _safe_comment(self, value: str) -> str:
        return value.replace("\n", " ").strip()
