from __future__ import annotations

from app.models.schemas import CodeGenerateRequest
from app.services.code_generator import ScraplingCodeGenerator


def test_generate_full_flow_scrapling_script_is_valid_python() -> None:
    generator = ScraplingCodeGenerator()
    script = generator.generate(
        CodeGenerateRequest(
            flowName="订单自动处理",
            flowDefinition={
                "inputVariables": [{"name": "base_url", "type": "String", "value": "https://quotes.toscrape.com/"}],
                "nodes": [
                    {"id": "start", "type": "start", "title": "开始"},
                    {"id": "open", "type": "browser.open", "title": "打开页面", "targetUrl": "${var.base_url}"},
                    {
                        "id": "extract",
                        "type": "browser.extract",
                        "title": "提取 Quote",
                        "selector": ".quote .text::text",
                        "extractMode": "text",
                        "outputVariable": "quotes",
                        "countVariable": "quote_count",
                    },
                    {"id": "end", "type": "end", "title": "结束"},
                ],
                "edges": [
                    {"source": "start", "target": "open"},
                    {"source": "open", "target": "extract"},
                    {"source": "extract", "target": "end"},
                ],
            },
        )
    )

    compile(script.content, script.filename, "exec")
    assert script.filename == "rpa-flow.py"
    assert "from scrapling.fetchers import DynamicFetcher, Fetcher, StealthyFetcher" in script.content
    assert "page = fetch_page(render_template(\"${var.base_url}\", variables), \"static\")" in script.content
    assert "variables[str(count_name)] = len(values)" in script.content
    assert "outputs[str(node.get('id') or node.get('title') or 'extract')] = values" in script.content


def test_generate_flow_script_preserves_unsupported_browser_actions_as_comments() -> None:
    generator = ScraplingCodeGenerator()
    script = generator.generate(
        CodeGenerateRequest(
            flowName="login-flow",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start", "title": "开始"},
                    {"id": "click", "type": "browser.click", "title": "点击登录", "selector": "#login"},
                ],
                "edges": [{"source": "start", "target": "click"}],
            },
        )
    )

    compile(script.content, script.filename, "exec")
    assert "Scrapling 是采集引擎，不执行真实点击/输入" in script.content
