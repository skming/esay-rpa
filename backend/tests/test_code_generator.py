from __future__ import annotations

import pytest

from app.models.schemas import CodeGenerateRequest
from app.services.code_generator import ScraplingCodeGenerator
from app.services.flow_control import evaluate_condition
from app.services.runtime_variables import RuntimeVariableStore

# 用 exec 加载生成脚本后替换网络抓取函数，验证脚本自身的控制流和数据输出。
# scrapling 已是后端依赖，import 不会失败。
def _exec_script(content: str) -> dict:
    namespace: dict = {}
    exec(compile(content, "<generated>", "exec"), namespace)
    return namespace


def _generate(flow_name: str, flow: dict) -> object:
    return ScraplingCodeGenerator().generate(
        CodeGenerateRequest(flowName=flow_name, flowDefinition=flow)
    )


def test_generate_full_flow_scrapling_script_is_valid_python() -> None:
    script = _generate(
        "订单自动处理",
        {
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

    compile(script.content, script.filename, "exec")
    assert "from scrapling.fetchers import DynamicFetcher, Fetcher, StealthyFetcher" in script.content
    assert 'page = fetch_page(render_template("${var.base_url}", variables), "static")' in script.content


def test_generate_flow_script_rejects_unsupported_browser_actions() -> None:
    with pytest.raises(ValueError, match=r"点击登录（browser\.click）"):
        _generate(
            "login-flow",
            {
                "nodes": [
                    {"id": "start", "type": "start", "title": "开始"},
                    {"id": "click", "type": "browser.click", "title": "点击登录", "selector": "#login"},
                ],
                "edges": [{"source": "start", "target": "click"}],
            },
        )


def test_disabled_unsupported_node_does_not_block_or_execute() -> None:
    script = _generate(
        "disabled-action",
        {
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "click", "type": "browser.click", "title": "停用点击", "disabled": True},
                {"id": "end", "type": "end"},
            ],
            "edges": [
                {"source": "start", "target": "click"},
                {"source": "click", "target": "end"},
            ],
        },
    )

    assert "已禁用，跳过" in script.content


def test_generated_pure_scraping_flow_runs_as_a_standalone_function() -> None:
    script = _generate(
        "quotes",
        {
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "open", "type": "browser.open", "targetUrl": "https://example.test"},
                {"id": "extract", "type": "browser.extract", "selector": ".quote", "outputVariable": "quotes"},
                {"id": "end", "type": "end"},
            ],
            "edges": [
                {"source": "start", "target": "open"},
                {"source": "open", "target": "extract"},
                {"source": "extract", "target": "end"},
            ],
        },
    )
    namespace = _exec_script(script.content)

    class Element:
        def get_all_text(self, *, strip: bool) -> str:
            assert strip is True
            return "captured"

    class Page:
        def css(self, selector: str) -> list[Element]:
            assert selector == ".quote"
            return [Element()]

    namespace["fetch_page"] = lambda _url, _fetcher="static": Page()

    result = namespace["run"]()

    assert result["variables"]["quotes"] == ["captured"]
    assert result["outputs"]["extract"] == ["captured"]
    assert script.dependencies == ["scrapling[all]>=0.4.10"]


def test_credential_values_never_reach_the_exported_script() -> None:
    """导出脚本是用户会下载、提交进仓库、转发给同事的文件。

    平台其它出口都对 credential/sensitive 变量脱敏（见 ai_flow_state._render_variables 只报
    已填/未填），唯独这里曾把明文写进脚本头部的 VARIABLES 字面量。
    """
    script = _generate(
        "登录抓取",
        {
            "inputVariables": [
                {"name": "shop_password", "type": "String", "value": "p@ssw0rd-LEAKED", "category": "credential"},
                {"name": "api_token", "type": "String", "value": "tok-LEAKED", "sensitive": True},
                {"name": "base_url", "type": "String", "value": "https://example.com"},
            ],
            "nodes": [{"id": "start", "type": "start"}],
            "edges": [],
        },
    )

    assert "p@ssw0rd-LEAKED" not in script.content
    assert "tok-LEAKED" not in script.content
    # 非敏感变量仍然内联，否则每跑一次都要先配一堆环境变量
    assert "https://example.com" in script.content
    assert "RPA_SHOP_PASSWORD" in script.content
    assert "RPA_API_TOKEN" in script.content


def test_missing_credential_env_var_fails_before_the_flow_runs(monkeypatch) -> None:
    """缺凭据必须在跑之前就停：带着空密码跑下去只会在某个页面上拿到登录页，
    而那不会报错，只会安静地抓回一堆错数据。"""
    script = _generate(
        "登录抓取",
        {
            "inputVariables": [{"name": "pwd", "type": "String", "value": "x", "category": "credential"}],
            "nodes": [{"id": "start", "type": "start"}],
            "edges": [],
        },
    )
    namespace = _exec_script(script.content)
    monkeypatch.delenv("RPA_PWD", raising=False)

    with pytest.raises(RuntimeError, match="RPA_PWD"):
        namespace["load_credentials"]()


def _branching_flow() -> dict:
    return {
        "inputVariables": [{"name": "row_count", "type": "Integer", "value": "0"}],
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "cond", "type": "control.condition", "title": "有数据吗", "condition": "row_count > 0"},
            {"id": "yes", "type": "browser.extract", "title": "抓明细", "selector": ".yes", "outputVariable": "v_yes"},
            {"id": "no", "type": "browser.extract", "title": "抓空态", "selector": ".no", "outputVariable": "v_no"},
            {"id": "end", "type": "end"},
        ],
        "edges": [
            {"source": "start", "target": "cond"},
            {"source": "cond", "target": "yes", "sourceHandle": "true"},
            {"source": "cond", "target": "no", "sourceHandle": "false"},
            {"source": "yes", "target": "end"},
            {"source": "no", "target": "end"},
        ],
    }


def test_condition_branches_become_real_if_else_not_two_sequential_calls() -> None:
    """两条腿顺序执行的脚本能跑完、能打印 JSON、不报错，产出的却是错数据。"""
    script = _generate("分支流程", _branching_flow())

    compile(script.content, script.filename, "exec")
    assert "if evaluate_condition(" in script.content
    assert "    else:" in script.content
    # 汇合节点只能出现一次，否则 end 之后的链路会被走两遍
    assert script.content.count('".yes"') == 1
    assert script.content.count('".no"') == 1


def test_generated_condition_evaluator_agrees_with_the_runtime_one() -> None:
    """导出脚本必须自带求值逻辑（不能 import 平台代码），于是同一套语义有了两份实现。

    两份一旦分叉，同一个流程在平台里和导出后会走不同的分支，而且两边都不报错。
    这条测试是这个复制的唯一约束，改动任一侧都必须让它保持绿。
    """
    namespace = _exec_script(_generate("分支流程", _branching_flow()).content)
    generated = namespace["evaluate_condition"]

    cases = [
        ("row_count > 0", {"row_count": 5}),
        ("row_count > 0", {"row_count": 0}),
        ("row_count >= 3", {"row_count": 3}),
        ("name == 'abc'", {"name": "abc"}),
        ("name != 'abc'", {"name": "abc"}),
        ("flag == true", {"flag": True}),
        ("flag", {"flag": False}),
        ("flag", {"flag": "是"}),
        ("missing", {}),
        ("total <= 2.5", {"total": "2.5"}),
    ]
    for expression, variables in cases:
        try:
            expected = evaluate_condition(
                {"type": "control.condition", "condition": expression},
                RuntimeVariableStore.from_initial(variables),
            )
        except Exception as exc:  # noqa: BLE001 - 只比较异常类型与文案
            expected = f"{type(exc).__name__}: {exc}"

        try:
            actual = generated(expression, variables)
        except Exception as exc:  # noqa: BLE001
            actual = f"{type(exc).__name__}: {exc}"

        # 变量名写错时运行时抛错，导出脚本必须同样抛错：静默取 None 会让错分支跑完还报成功
        assert actual == expected, f"分叉：{expression} with {variables}"


def test_foreach_emits_a_real_loop_over_the_items_variable() -> None:
    script = _generate(
        "逐项抓取",
        {
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "loop", "type": "control.foreach", "itemsVariable": "links", "itemVariable": "link"},
                {"id": "body", "type": "browser.open", "title": "打开详情", "targetUrl": "${var.link}"},
                {"id": "after", "type": "file.write", "title": "落盘", "path": "out.txt", "content": "done"},
            ],
            "edges": [
                {"source": "start", "target": "loop"},
                {"source": "loop", "target": "body", "sourceHandle": "body"},
                {"source": "loop", "target": "after", "sourceHandle": "exit"},
                {"source": "body", "target": "loop"},
            ],
        },
    )

    compile(script.content, script.filename, "exec")
    assert "for loop_index, item in enumerate(" in script.content
    # 循环体在 for 内（缩进更深），退出边在 for 外
    body_line = next(line for line in script.content.splitlines() if "打开详情" in line)
    after_line = next(line for line in script.content.splitlines() if "落盘" in line)
    assert len(body_line) - len(body_line.lstrip()) > len(after_line) - len(after_line.lstrip())


def test_repeat_until_emits_a_bounded_while_loop() -> None:
    script = _generate(
        "翻到目标月份",
        {
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "rep", "type": "control.repeat_until", "condition": "done == true", "maxIterations": 12},
                {"id": "body", "type": "browser.extract", "title": "读当前月", "selector": ".m", "outputVariable": "m"},
            ],
            "edges": [
                {"source": "start", "target": "rep"},
                {"source": "rep", "target": "body", "sourceHandle": "body"},
                {"source": "body", "target": "rep"},
            ],
        },
    )

    compile(script.content, script.filename, "exec")
    assert "while " in script.content
    assert "12" in script.content
    # 跑满上限说明退出条件始终没满足，与运行时一致必须报错而不是静默往下走
    assert "repeat_until" in script.content


def test_chinese_flow_names_do_not_all_collapse_to_one_filename() -> None:
    """两个中文流程导出后同名，第二个会覆盖第一个。"""
    first = _generate("门店合约抓取", {"nodes": [], "edges": []}).filename
    second = _generate("订单导出", {"nodes": [], "edges": []}).filename

    assert first != second
    assert first.endswith(".py") and second.endswith(".py")


def test_text_extraction_reads_descendant_text_like_the_runtime_does() -> None:
    """element.text 只取节点自身文本，<td><span>值</span></td> 会返回空串。

    运行时已在 scrapling_runner 改用 get_all_text；导出脚本留在旧写法上就等于
    同一个流程平台里有数据、导出后一列空值。
    """
    script = _generate(
        "表格抓取",
        {
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "e", "type": "browser.extract", "selector": "td", "outputVariable": "cells"},
            ],
            "edges": [{"source": "start", "target": "e"}],
        },
    )

    assert "get_all_text(strip=True)" in script.content
    assert "element.text" not in script.content


def test_click_based_pagination_refuses_instead_of_scraping_page_one() -> None:
    """Scrapling 不执行点击，翻页节点此前静默降级成 pass：只抓第 1 页且报成功。"""
    flow = {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "p", "type": "browser.paginateNext", "selector": "a.next", "targetSelector": ".row::text"},
        ],
        "edges": [{"source": "start", "target": "p"}],
    }

    with pytest.raises(ValueError, match="翻页"):
        _generate("翻页抓取", flow)


def test_url_based_pagination_becomes_a_page_loop() -> None:
    script = _generate(
        "翻页抓取",
        {
            "nodes": [
                {"id": "start", "type": "start"},
                {
                    "id": "p",
                    "type": "browser.paginateNext",
                    "urlTemplate": "https://example.com/list?p=${page}",
                    "startPage": 1,
                    "pageStep": 1,
                    "maxIterations": 5,
                    "targetSelector": ".row::text",
                    "outputVariable": "rows",
                },
            ],
            "edges": [{"source": "start", "target": "p"}],
        },
    )

    compile(script.content, script.filename, "exec")
    # 循环变量不能占用 page：下游节点靠 page 拿当前文档
    assert "for page_number in range(" in script.content
    assert '${page}' in script.content


def test_count_mode_reports_the_real_count_not_one() -> None:
    """count 模式返回的是长度为 1 的列表，countVariable 取 len(values) 恒等于 1。"""
    script = _generate(
        "计数",
        {
            "nodes": [
                {"id": "start", "type": "start"},
                {
                    "id": "c",
                    "type": "browser.extract",
                    "selector": ".row",
                    "extractMode": "count",
                    "outputVariable": "rows",
                    "countVariable": "row_count",
                },
            ],
            "edges": [{"source": "start", "target": "c"}],
        },
    )
    namespace = _exec_script(script.content)
    variables: dict = {}

    namespace["save_output"](
        variables,
        {"outputVariable": "rows", "countVariable": "row_count"},
        ["7"],
        mode="count",
    )

    assert variables["row_count"] == 7
