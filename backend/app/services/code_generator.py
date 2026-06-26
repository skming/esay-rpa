from __future__ import annotations

import json
import re

from app.models.schemas import CodeGenerateRequest, GeneratedScript


class ScraplingCodeGenerator:
    def generate(self, request: CodeGenerateRequest) -> GeneratedScript:
        flow_name = request.flow_name.strip()
        filename = f"{self._slugify(flow_name)}.py"
        content = "\n".join(self._build_script_lines(request))

        return GeneratedScript(
            filename=filename,
            dependencies=["scrapling[all]>=0.3.0"],
            content=content,
        )

    def _build_script_lines(self, request: CodeGenerateRequest) -> list[str]:
        fetcher_import = {
            "static": "Fetcher",
            "dynamic": "DynamicFetcher",
            "stealthy": "StealthyFetcher",
        }[request.fetcher]
        fetch_call = {
            "static": f"Fetcher.get({json.dumps(str(request.target_url))})",
            "dynamic": f"DynamicFetcher.fetch({json.dumps(str(request.target_url))}, headless=True, network_idle=True)",
            "stealthy": f"StealthyFetcher.fetch({json.dumps(str(request.target_url))}, headless=True, network_idle=True)",
        }[request.fetcher]

        selector_expression = self._selector_expression(request)
        return [
            "from __future__ import annotations",
            "",
            f"from scrapling.fetchers import {fetcher_import}",
            "",
            "",
            "def run() -> list[str]:",
            f"    \"\"\"运行从 Easy RPA 生成的 {self._safe_doc(request.flow_name)} 采集脚本。\"\"\"",
            f"    page = {fetch_call}",
            f"    values = {selector_expression}",
            "    if not values:",
            f"        raise RuntimeError({json.dumps(f'未找到目标元素: {request.selector}')})",
            "    for value in values:",
            "        print(value)",
            "    return values",
            "",
            "",
            "if __name__ == \"__main__\":",
            "    run()",
            "",
        ]

    def _selector_expression(self, request: CodeGenerateRequest) -> str:
        selector = str(request.selector)
        adaptive_args = []
        if request.adaptive:
            adaptive_args.append("adaptive=True")
        if request.auto_save:
            adaptive_args.append("auto_save=True")
        suffix = f", {', '.join(adaptive_args)}" if adaptive_args else ""

        if request.extract_mode == "attribute":
            attribute = request.attribute or "href"
            return f"page.css({json.dumps(f'{selector}::attr({attribute})')}).getall()"

        if request.extract_mode == "html":
            return f"[element.html_content for element in page.css({json.dumps(selector)}{suffix})]"

        if "::text" in selector:
            return f"page.css({json.dumps(selector)}{suffix}).getall()"

        return f"[element.text for element in page.css({json.dumps(selector)}{suffix})]"

    def _slugify(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug or "rpa-flow"

    def _safe_doc(self, value: str) -> str:
        return value.replace('"""', "").replace("\n", " ").strip() or "RPA 流程"
