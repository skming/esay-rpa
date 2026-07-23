from __future__ import annotations

import json
import re
from typing import Any

from app.models.schemas import CodeGenerateRequest, GeneratedScript


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

        nodes = self._ordered_nodes(flow)
        variables = self._read_input_variables(flow)
        lines = [
            "from __future__ import annotations",
            "",
            "import json",
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
            "",
            "",
            "def render_template(value: Any, variables: dict[str, Any]) -> str:",
            "    text = '' if value is None else str(value)",
            "    return re.sub(r'\\$\\{var\\.([^}]+)\\}', lambda match: str(variables.get(match.group(1), '')), text)",
            "",
            "",
            "def fetch_page(url: str, fetcher: str = 'static') -> Any:",
            "    if fetcher == 'dynamic':",
            "        return DynamicFetcher.fetch(url, headless=True, network_idle=True)",
            "    if fetcher == 'stealthy':",
            "        return StealthyFetcher.fetch(url, headless=True, network_idle=True)",
            "    return Fetcher.get(url)",
            "",
            "",
            "def extract_values(page: Any, selector: str, mode: str = 'text', attribute: str | None = None) -> list[str]:",
            "    if page is None:",
            "        raise RuntimeError('页面尚未打开，无法提取数据')",
            "    if mode == 'attribute':",
            "        attr = attribute or 'href'",
            "        return [str(value) for value in page.css(f'{selector}::attr({attr})').getall()]",
            "    if mode == 'html':",
            "        return [str(element.html_content) for element in page.css(selector)]",
            "    if mode == 'count':",
            "        return [str(len(page.css(selector)))]",
            "    if '::text' in selector:",
            "        return [str(value) for value in page.css(selector).getall()]",
            "    return [str(element.text) for element in page.css(selector)]",
            "",
            "",
            "def save_output(variables: dict[str, Any], node: dict[str, Any], values: list[str]) -> None:",
            "    output_name = node.get('outputVariable') or node.get('responseVariable')",
            "    if output_name:",
            "        variables[str(output_name)] = values",
            "    first_name = node.get('firstValueVariable')",
            "    if first_name:",
            "        variables[str(first_name)] = values[0] if values else ''",
            "    count_name = node.get('countVariable') or node.get('statusVariable')",
            "    if count_name:",
            "        variables[str(count_name)] = len(values)",
            "",
            "",
            "def run() -> dict[str, Any]:",
            f"    \"\"\"运行从 Easy RPA 生成的 {self._safe_doc(request.flow_name)} 全流程 Scrapling 脚本。\"\"\"",
            "    variables = dict(VARIABLES)",
            "    page = None",
            "    outputs: dict[str, Any] = {}",
        ]

        for node in nodes:
            lines.extend(self._node_lines(node))

        lines.extend(
            [
                "    return {'variables': variables, 'outputs': outputs}",
                "",
                "",
                "if __name__ == '__main__':",
                "    result = run()",
                "    print(json.dumps(result, ensure_ascii=False, indent=2))",
                "",
            ]
        )
        return lines

    def _node_lines(self, node: dict[str, Any]) -> list[str]:
        node_type = str(node.get("type") or "")
        title = str(node.get("title") or node.get("id") or node_type)
        lines = [f"    # {self._safe_comment(title)} · {self._safe_comment(node_type)}"]

        if node_type in {"start", "end"}:
            lines.append("    pass")
            return lines

        if node.get("disabled") is True:
            lines.append("    # 已禁用，跳过。")
            lines.append("    pass")
            return lines

        if node_type in {"browser.open", "browser.tab.open", "browser.fetch"}:
            url_expr = self._node_string_expr(node.get("targetUrl") or node.get("url"))
            fetcher = str(node.get("fetcher") or "static")
            lines.append(f"    page = fetch_page(render_template({url_expr}, variables), {json.dumps(fetcher)})")
            if node_type == "browser.fetch" and node.get("selector"):
                lines.extend(self._extract_lines(node))
            return lines

        if node_type in {"browser.extract", "ui.extract"}:
            lines.extend(self._extract_lines(node))
            return lines

        if node_type in {"control.delay"}:
            delay_ms = int(node.get("delayMs") or node.get("timeoutMs") or 1000)
            lines.append(f"    time.sleep({max(delay_ms, 0) / 1000:.3f})")
            return lines

        if node_type in {"variable.set", "variable.assign", "variable.step"}:
            name = node.get("variableName") or node.get("outputVariable") or node.get("responseVariable")
            value = node.get("value") if node.get("value") is not None else node.get("defaultValue")
            if name:
                lines.append(f"    variables[{json.dumps(str(name))}] = render_template({self._node_string_expr(value)}, variables)")
            else:
                lines.append("    # 缺少 variableName，跳过变量写入。")
                lines.append("    pass")
            return lines

        if node_type == "file.write":
            path = self._node_string_expr(node.get("path"))
            content = self._node_string_expr(node.get("content") or node.get("value"))
            lines.append(f"    Path(render_template({path}, variables)).write_text(render_template({content}, variables), encoding='utf-8')")
            return lines

        if node_type.startswith("browser.") or node_type.startswith("ui."):
            lines.append("    # Scrapling 是采集引擎，不执行真实点击/输入；此交互节点已保留为注释。")
            lines.append("    pass")
            return lines

        lines.append("    # 当前节点类型暂未映射到 Scrapling 脚本，已保留为流程注释。")
        lines.append("    pass")
        return lines

    def _extract_lines(self, node: dict[str, Any]) -> list[str]:
        selector = self._node_string_expr(node.get("selector"))
        mode = str(node.get("extractMode") or "text")
        attribute = node.get("attribute")
        return [
            f"    values = extract_values(page, render_template({selector}, variables), {json.dumps(mode)}, {json.dumps(attribute)})",
            f"    if not values and not {json.dumps(bool(node.get('continueOnError')))}:",
            f"        raise RuntimeError({json.dumps('未找到目标元素: ' + str(node.get('selector') or ''))})",
            f"    node = {json.dumps(self._output_node_payload(node), ensure_ascii=False)}",
            "    save_output(variables, node, values)",
            "    outputs[str(node.get('id') or node.get('title') or 'extract')] = values",
        ]

    def _ordered_nodes(self, flow: dict[str, object]) -> list[dict[str, Any]]:
        # DFS 从 start 出发按边序遍历；Scrapling 脚本是线性执行，不支持条件分支/循环，
        # 多出边节点只会走到第一条，这里只求生成一份"能跑但可能不完整"的近似脚本。
        raw_nodes = flow.get("nodes")
        if not isinstance(raw_nodes, list):
            return []
        nodes = [node for node in raw_nodes if isinstance(node, dict)]
        by_id = {str(node.get("id")): node for node in nodes if node.get("id") is not None}
        edges = flow.get("edges")
        next_by_source: dict[str, list[str]] = {}
        if isinstance(edges, list):
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                source = edge.get("source")
                target = edge.get("target")
                if isinstance(source, str) and isinstance(target, str):
                    next_by_source.setdefault(source, []).append(target)

        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in seen:
                return
            node = by_id.get(node_id)
            if node is None:
                return
            seen.add(node_id)
            ordered.append(node)
            for target_id in next_by_source.get(node_id, []):
                visit(target_id)

        visit("start")
        for node in nodes:
            node_id = str(node.get("id"))
            if node_id not in seen:
                visit(node_id)
        return ordered

    def _read_input_variables(self, flow: dict[str, object]) -> dict[str, Any]:
        raw_variables = flow.get("inputVariables")
        if not isinstance(raw_variables, list):
            return {}
        variables: dict[str, Any] = {}
        for item in raw_variables:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            variables[name.strip()] = self._parse_variable_value(str(item.get("value") or ""), str(item.get("type") or "String"))
        return variables

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

    def _output_node_payload(self, node: dict[str, Any]) -> dict[str, Any]:
        keys = ("id", "title", "outputVariable", "responseVariable", "firstValueVariable", "countVariable", "statusVariable")
        return {key: node[key] for key in keys if key in node and node[key] is not None}

    def _node_string_expr(self, value: object) -> str:
        return json.dumps("" if value is None else str(value), ensure_ascii=False)

    def _slugify(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug or "rpa-flow"

    def _safe_doc(self, value: str) -> str:
        return value.replace('"""', "").replace("\n", " ").strip() or "RPA 流程"

    def _safe_comment(self, value: str) -> str:
        return value.replace("\n", " ").strip()
