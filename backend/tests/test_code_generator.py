from __future__ import annotations

from app.models.schemas import CodeGenerateRequest
from app.services.code_generator import ScraplingCodeGenerator


def test_generate_static_scrapling_script_is_valid_python() -> None:
    generator = ScraplingCodeGenerator()
    script = generator.generate(
        CodeGenerateRequest(
            flowName="订单自动处理",
            targetUrl="https://quotes.toscrape.com/",
            selector=".quote .text::text",
            extractMode="text",
        )
    )

    compile(script.content, script.filename, "exec")
    assert script.filename == "rpa-flow.py"
    assert "from scrapling.fetchers import Fetcher" in script.content
    assert "page.css(\".quote .text::text\").getall()" in script.content


def test_generate_dynamic_attribute_script() -> None:
    generator = ScraplingCodeGenerator()
    script = generator.generate(
        CodeGenerateRequest(
            flowName="links",
            targetUrl="https://example.com/",
            selector="a",
            fetcher="dynamic",
            extractMode="attribute",
            attribute="href",
        )
    )

    compile(script.content, script.filename, "exec")
    assert script.filename == "links.py"
    assert "DynamicFetcher.fetch" in script.content
    assert "a::attr(href)" in script.content
