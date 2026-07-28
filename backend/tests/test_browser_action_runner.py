from __future__ import annotations

import pytest

from app.services.browser_action_runner import BrowserActionContext, BrowserActionResult, BrowserActionRunner, _normalize_table_rows, _goto_with_retry, _raise_if_table_scope_error, apply_browser_result_variables, detect_blocking_overlay
from app.services.runtime_variables import RuntimeVariableStore


def test_normalize_table_rows_preserves_empty_cell_to_keep_column_alignment() -> None:
    """Regression test for a table-extract column misalignment: rows where an
    owner column is unassigned have a blank cell in that position. Dropping the
    blank cell instead of keeping it as '' shifts every later column one
    position left for that row — exactly the corruption seen in the exported
    table. Only a row that is empty across every column should be dropped.
    Uses synthetic placeholder values, not real collected data."""
    raw_rows = [
        ["owner-a", "2026-07-07 14:38:41", "action-x"],
        ["owner-a", "2026-07-07 10:26:28", "action-y"],
        ["owner-b", "2026-06-23 10:54:48", "action-x"],
        ["", "2026-05-27 13:58:15", "action-x"],
        ["", "2026-05-21 09:39:28", "action-y"],
        ["", "", ""],
    ]

    rows = _normalize_table_rows(raw_rows)

    assert rows == [
        ["owner-a", "2026-07-07 14:38:41", "action-x"],
        ["owner-a", "2026-07-07 10:26:28", "action-y"],
        ["owner-b", "2026-06-23 10:54:48", "action-x"],
        ["", "2026-05-27 13:58:15", "action-x"],
        ["", "2026-05-21 09:39:28", "action-y"],
    ]
    # Column position must survive the empty cell — the owner column (index 0)
    # stays blank rather than later columns sliding left into it.
    assert rows[3][0] == ""
    assert rows[3][1] == "2026-05-27 13:58:15"


def test_apply_browser_result_variables_writes_list_first_and_count() -> None:
    variables = RuntimeVariableStore.from_initial({})
    node = {
        "outputVariable": "items",
        "firstValueVariable": "first_item",
        "countVariable": "item_count",
    }
    result = BrowserActionResult(action_type="browser.extract", detail=".item", values=["A", "B"])

    saved_names = apply_browser_result_variables(node, result, variables)
    snapshots = {variable.name: variable for variable in variables.snapshots()}

    assert saved_names == ["items", "first_item", "item_count"]
    assert snapshots["items"].value == '["A", "B"]'
    assert snapshots["items"].type == "List"
    assert snapshots["first_item"].value == "A"
    assert snapshots["item_count"].value == "2"


def test_apply_browser_result_variables_accepts_response_variable_alias() -> None:
    variables = RuntimeVariableStore.from_initial({})
    node = {"responseVariable": "ui_values"}
    result = BrowserActionResult(action_type="ui.extract", detail=".result", values=["A", "B"])

    saved_names = apply_browser_result_variables(node, result, variables)
    snapshots = {variable.name: variable for variable in variables.snapshots()}

    assert saved_names == ["ui_values"]
    assert snapshots["ui_values"].value == '["A", "B"]'
    assert snapshots["ui_values"].type == "List"


def test_apply_browser_result_variables_appends_record_payload() -> None:
    variables = RuntimeVariableStore.from_initial({})
    node = {"appendVariable": "detail_records", "appendMode": "record"}
    result = BrowserActionResult(action_type="browser.extract", detail="article", values=["正文"])

    saved_names = apply_browser_result_variables(node, result, variables)

    assert saved_names == ["detail_records"]
    assert variables.get("detail_records") == [{"count": 1, "detail": "article", "first": "正文", "values": ["正文"]}]


async def test_browser_extract_reads_attribute_values() -> None:
    variables = RuntimeVariableStore.from_initial({})
    page = FakePage(["/a", "", None, "/b"])
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    result = await BrowserActionRunner().run(
        {"type": "browser.extract", "selector": "a.article", "extractMode": "attribute", "attribute": "href"},
        variables,
        context,
        timeout_ms=1000,
    )

    assert page.waited_selector == "a.article"
    assert page.locator_selector == "a.article"
    assert page.attribute == "href"
    assert page.used_dom_property_lookup
    assert result.values == ["/a", "/b"]


async def test_browser_extract_supports_selector_attribute_suffix() -> None:
    variables = RuntimeVariableStore.from_initial({})
    page = FakePage(["https://example.com/a"])
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    result = await BrowserActionRunner().run(
        {"type": "browser.extract", "selector": "a.article::attr(href)"},
        variables,
        context,
        timeout_ms=1000,
    )

    assert page.waited_selector == "a.article"
    assert page.locator_selector == "a.article"
    assert page.attribute == "href"
    assert result.values == ["https://example.com/a"]


async def test_browser_extract_supports_selector_text_suffix() -> None:
    variables = RuntimeVariableStore.from_initial({})
    page = FakePage([" 标题 "])
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    result = await BrowserActionRunner().run(
        {"type": "browser.extract", "selector": ".title::text"},
        variables,
        context,
        timeout_ms=1000,
    )

    assert page.waited_selector == ".title"
    assert page.locator_selector == ".title"
    assert result.values == ["标题"]


class FakeWaitForPage:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts
        self._text_calls = 0
        self.waited_states: list[str] = []

    def locator(self, selector: str) -> "FakeWaitForPage":
        return self

    @property
    def first(self) -> "FakeWaitForPage":
        return self

    async def wait_for(self, *, state: str, timeout: int) -> None:
        self.waited_states.append(state)

    async def text_content(self) -> str:
        text = self._texts[min(self._text_calls, len(self._texts) - 1)]
        self._text_calls += 1
        return text

    async def wait_for_timeout(self, delay_ms: int) -> None:
        pass


async def test_browser_wait_for_defaults_to_visible_state() -> None:
    variables = RuntimeVariableStore.from_initial({})
    page = FakeWaitForPage([])
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    result = await BrowserActionRunner().run(
        {"type": "browser.waitFor", "selector": "#result"},
        variables,
        context,
        timeout_ms=1000,
    )

    assert page.waited_states == ["visible"]
    assert result.values == ["#result"]


async def test_browser_wait_for_hidden_passes_hidden_state() -> None:
    variables = RuntimeVariableStore.from_initial({})
    page = FakeWaitForPage([])
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    await BrowserActionRunner().run(
        {"type": "browser.waitFor", "selector": ".loading-mask", "waitCondition": "hidden"},
        variables,
        context,
        timeout_ms=1000,
    )

    assert page.waited_states == ["hidden"]


async def test_browser_wait_for_text_contains_polls_until_match() -> None:
    variables = RuntimeVariableStore.from_initial({})
    page = FakeWaitForPage(["正在处理...", "处理完成，共导出 12 条"])
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    result = await BrowserActionRunner().run(
        {
            "type": "browser.waitFor",
            "selector": "#status",
            "waitCondition": "textContains",
            "inputValue": "处理完成",
        },
        variables,
        context,
        timeout_ms=2000,
    )

    assert page._text_calls == 2
    assert result.values == ["#status"]


async def test_browser_wait_for_text_contains_times_out_when_text_never_matches() -> None:
    variables = RuntimeVariableStore.from_initial({})
    page = FakeWaitForPage(["正在处理..."])
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    try:
        await BrowserActionRunner().run(
            {
                "type": "browser.waitFor",
                "selector": "#status",
                "waitCondition": "textContains",
                "inputValue": "处理完成",
            },
            variables,
            context,
            timeout_ms=50,
        )
    except TimeoutError as exc:
        assert "等待文本超时" in str(exc)
    else:
        raise AssertionError("expected TimeoutError")


async def test_browser_extract_reads_html_values() -> None:
    variables = RuntimeVariableStore.from_initial({})
    page = FakePage(["<strong>标题</strong>", ""])
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    result = await BrowserActionRunner().run(
        {"type": "browser.extract", "selector": ".content", "extractMode": "html"},
        variables,
        context,
        timeout_ms=1000,
    )

    assert page.waited_selector == ".content"
    assert page.locator_selector == ".content"
    assert page.used_inner_html_lookup
    assert result.values == ["<strong>标题</strong>"]


async def test_browser_extract_count_mode_returns_dom_match_count() -> None:
    variables = RuntimeVariableStore.from_initial({})
    page = FakePage(["", ""])
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    result = await BrowserActionRunner().run(
        {"type": "browser.extract", "selector": "input[type='password']", "extractMode": "count", "countVariable": "login_count"},
        variables,
        context,
        timeout_ms=1000,
    )
    saved_names = apply_browser_result_variables({"extractMode": "count", "countVariable": "login_count"}, result, variables)

    assert saved_names == ["login_count"]
    assert result.values == ["2"]
    assert variables.get("login_count") == 2


async def test_browser_press_sends_variable_resolved_key_to_selector() -> None:
    variables = RuntimeVariableStore.from_initial({"submit_key": "Enter"})
    page = FakePressPage()
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    result = await BrowserActionRunner().run(
        {"type": "browser.press", "selector": "input.search", "inputValue": "${var.submit_key}"},
        variables,
        context,
        timeout_ms=1000,
    )

    assert page.pressed == [("input.search", "Enter", 1000)]
    assert result.detail == "input.search"
    assert result.values == ["Enter"]


async def test_browser_press_sends_page_level_key_without_selector_lookup() -> None:
    variables = RuntimeVariableStore.from_initial({})
    page = FakePressPage()
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    result = await BrowserActionRunner().run(
        {"type": "browser.press", "selector": "body", "inputValue": "Escape"},
        variables,
        context,
        timeout_ms=1000,
    )

    assert page.pressed == []
    assert page.keyboard.pressed == ["Escape"]
    assert result.detail == "body"
    assert result.values == ["Escape"]


async def test_browser_click_load_more_clicks_until_no_growth_and_extracts_values() -> None:
    variables = RuntimeVariableStore.from_initial({})
    page = FakeLoadMorePage()
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    result = await BrowserActionRunner().run(
        {
            "type": "browser.clickLoadMore",
            "selector": "button.load-more",
            "targetSelector": ".product-card::text",
            "maxIterations": 5,
            "delayMs": 100,
            "loadedCountVariable": "loaded_dom_count",
        },
        variables,
        context,
        timeout_ms=1000,
    )

    assert page.clicked_count == 3
    assert page.waited_timeouts == [100, 100, 100]
    assert page.waited_selectors == [".product-card"]
    assert result.detail == "button.load-more -> .product-card · 4 轮加载 · [0, 1, 2] · stop=no_new_items"
    assert result.values == ["A", "B"]
    assert variables.get("loaded_dom_count") == 2


async def test_browser_paginate_next_collects_each_page_until_button_disabled() -> None:
    variables = RuntimeVariableStore.from_initial({})
    page = FakePaginatePage()
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    result = await BrowserActionRunner().run(
        {
            "type": "browser.paginateNext",
            "selector": "a.next",
            "targetSelector": ".row::text",
            "maxIterations": 10,
            "delayMs": 50,
            "pageCountVariable": "visited_pages",
        },
        variables,
        context,
        timeout_ms=1000,
    )

    assert page.clicked_count == 2
    assert page.waited_timeouts == [50, 50]
    assert result.detail == "a.next -> .row · 3 页 · [2, 2, 1] · stop=next_button_disabled"
    assert result.values == ["A1", "A2", "B1", "B2", "C1"]
    assert variables.get("visited_pages") == 3


async def test_browser_paginate_next_fails_loudly_when_next_selector_matches_nothing() -> None:
    """配了 paginateNext 就是断言「这里有下一页」，断言不成立必须当场失败。

    否则表现是 success + 只有第 1 页的数据，没有任何字段能看出残缺。
    """
    variables = RuntimeVariableStore.from_initial({})
    page = FakeUnmatchedPaginatePage()
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    with pytest.raises(ValueError, match="分页未生效"):
        await BrowserActionRunner().run(
            {
                "type": "browser.paginateNext",
                "selector": "a.normal_page_right",
                "targetSelector": ".row::text",
            },
            variables,
            context,
            timeout_ms=1000,
        )


async def test_browser_paginate_next_picks_union_selector_candidates_in_written_order() -> None:
    """逗号组按书写顺序取第一组有命中的，不按 DOM 顺序。

    `locator("A, B")` 返回 DOM 里靠前的那个，扩展执行器返回写在前面的那个；同一份流程
    两个执行器点到不同元素。写「下一页, 第2页链接」时 DOM 顺序会在第 3 页点回第 2 页，
    翻页被判成 duplicate_content 提前收工，且仍然 success。
    """
    variables = RuntimeVariableStore.from_initial({})
    page = FakeUnionPaginatePage()
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    result = await BrowserActionRunner().run(
        {
            "type": "browser.paginateNext",
            "selector": 'a.next_page, a.page_normal[href*="?p=2"]',
            "targetSelector": ".row::text",
            "outputVariable": "rows",
        },
        variables,
        context,
        timeout_ms=1000,
    )

    assert result.rows == ["A1", "A2", "B1", "B2", "C1"]
    assert page.clicked_selectors == ["a.next_page", "a.next_page"]


async def test_browser_paginate_next_keeps_running_when_locator_cannot_report_match_count() -> None:
    """报不出匹配数 != 匹配数为 0，否则不支持 count() 的 locator 会被误判成分页坏了。"""
    variables = RuntimeVariableStore.from_initial({})
    page = FakePaginatePage()
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    result = await BrowserActionRunner().run(
        {"type": "browser.paginateNext", "selector": "a.next", "targetSelector": ".row::text"},
        variables,
        context,
        timeout_ms=1000,
    )

    assert result.values == ["A1", "A2", "B1", "B2", "C1"]


async def test_browser_dismiss_clicks_visible_candidates_and_waits_target() -> None:
    variables = RuntimeVariableStore.from_initial({})
    page = FakeDismissPage()
    context = BrowserActionContext(playwright=object(), browser=object(), page=page)

    result = await BrowserActionRunner().run(
        {
            "type": "browser.dismiss",
            "selector": "button.accept-cookie\nbutton.close-subscribe",
            "targetSelector": ".content-ready",
            "delayMs": 120,
            "dismissedCountVariable": "dismissed_count",
        },
        variables,
        context,
        timeout_ms=1000,
    )

    assert page.clicked_selectors == ["button.accept-cookie"]
    assert page.waited_selectors == [".content-ready"]
    assert page.waited_timeouts == [120]
    assert result.values == ["1"]
    assert variables.get("dismissed_count") == 1


class FakeOverlayEvaluatePage:
    def __init__(self, result: object) -> None:
        self._result = result

    async def evaluate(self, script: str, arg: object = None) -> object:
        return self._result


async def test_detect_blocking_overlay_recognizes_captcha_by_keyword() -> None:
    page = FakeOverlayEvaluatePage(
        {
            "reason": "target_obscured",
            "vendor": None,
            "tag": "div",
            "id": "mask",
            "className": "verify-mask",
            "text": "请完成验证后继续 拖动滑块完成拼图",
            "interactive": [],
            "hasIframe": False,
        }
    )

    overlay = await detect_blocking_overlay(page, "#submit")

    assert overlay is not None
    assert overlay.label == "疑似验证码"


async def test_detect_blocking_overlay_ignores_low_confidence_fullscreen_fallback() -> None:
    """兜底扫描命中大面积定位容器，但没有厂商特征也没有关键词命中时，很可能只是
    页面自身的布局容器（如 SPA 应用外壳）而不是真的阻断浮层——不应据此转人工。"""
    page = FakeOverlayEvaluatePage(
        {
            "reason": "fullscreen_overlay",
            "vendor": None,
            "tag": "div",
            "id": "app-shell",
            "className": "app-root fixed-layout",
            "text": "首页 商品 购物车 我的",
            "interactive": [],
            "hasIframe": False,
        }
    )

    overlay = await detect_blocking_overlay(page, None)

    assert overlay is None


async def test_detect_blocking_overlay_keeps_low_confidence_target_obscured() -> None:
    """target_obscured 信号本身已经证明目标元素被遮挡，即使标签退化为"未知弹层"
    也仍应转人工，不受兜底扫描的置信度收紧影响。"""
    page = FakeOverlayEvaluatePage(
        {
            "reason": "target_obscured",
            "vendor": None,
            "tag": "div",
            "id": "mystery-modal",
            "className": "modal-xyz",
            "text": "",
            "interactive": [],
            "hasIframe": False,
        }
    )

    overlay = await detect_blocking_overlay(page, "#submit")

    assert overlay is not None
    assert overlay.label == "未知弹层"


class FakePage:
    def __init__(self, values: list[str | None]) -> None:
        self._values = values
        self.waited_selector: str | None = None
        self.locator_selector: str | None = None
        self.attribute: str | None = None
        self.used_dom_property_lookup = False
        self.used_inner_html_lookup = False

    def locator(self, selector: str) -> "FakePage":
        self.locator_selector = selector
        return self

    @property
    def first(self) -> "FakePage":
        return self

    async def wait_for(self, *, state: str, timeout: int) -> None:
        self.waited_selector = self.locator_selector

    async def evaluate_all(self, script: str, attribute: str | None = None) -> list[str | None]:
        if "innerHTML" in script:
            self.used_inner_html_lookup = True
            return self._values
        self.attribute = attribute
        self.used_dom_property_lookup = "attribute in element" in script and "element[attribute]" in script
        return self._values

    async def all_text_contents(self) -> list[str | None]:
        return self._values

    async def count(self) -> int:
        return len(self._values)


class FakeLoadMorePage:
    def __init__(self) -> None:
        self.clicked_count = 0
        self.waited_selectors: list[str] = []
        self.waited_timeouts: list[int] = []

    def locator(self, selector: str) -> "FakeLoadMoreLocator":
        return FakeLoadMoreLocator(self, selector)

    async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
        self.waited_selectors.append(selector)

    async def wait_for_timeout(self, delay_ms: int) -> None:
        self.waited_timeouts.append(delay_ms)


class FakePressPage:
    def __init__(self) -> None:
        self.pressed: list[tuple[str, str, int]] = []
        self.keyboard = FakeKeyboard()
        self._locator_selector = ""

    def locator(self, selector: str) -> "FakePressPage":
        self._locator_selector = selector
        return self

    @property
    def first(self) -> "FakePressPage":
        return self

    async def press(self, key: str, *, timeout: int) -> None:
        self.pressed.append((self._locator_selector, key, timeout))


class FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    async def press(self, key: str) -> None:
        self.pressed.append(key)


class FakeLoadMoreLocator:
    def __init__(self, page: FakeLoadMorePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    def first(self) -> "FakeLoadMoreLocator":
        return self

    async def wait_for(self, *, state: str, timeout: int) -> None:
        self._page.waited_selectors.append(self._selector)

    async def is_visible(self) -> bool:
        return self._page.clicked_count < 3

    async def click(self, *, timeout: int) -> None:
        self._page.clicked_count += 1

    async def count(self) -> int:
        if self._selector != ".product-card":
            return 1
        return min(self._page.clicked_count, 2)

    async def all_text_contents(self) -> list[str | None]:
        return ["A", "B"][: min(self._page.clicked_count, 2)]

    async def evaluate_all(self, script: str, attribute: str | None = None) -> list[str | None]:
        return await self.all_text_contents()


class FakePaginatePage:
    def __init__(self) -> None:
        self.page_index = 0
        self.clicked_count = 0
        self.waited_timeouts: list[int] = []
        self.pages = [["A1", "A2"], ["B1", "B2"], ["C1"]]

    @property
    def url(self) -> str:
        return f"https://example.com/list?page={self.page_index + 1}"

    def locator(self, selector: str) -> "FakePaginateLocator":
        return FakePaginateLocator(self, selector)

    async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
        return None

    async def wait_for_timeout(self, delay_ms: int) -> None:
        self.waited_timeouts.append(delay_ms)


class FakePaginateLocator:
    def __init__(self, page: FakePaginatePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    def first(self) -> "FakePaginateLocator":
        return self

    async def wait_for(self, *, state: str, timeout: int) -> None:
        return None

    async def is_visible(self) -> bool:
        return True

    async def is_disabled(self) -> bool:
        return self._selector == "a.next" and self._page.page_index >= len(self._page.pages) - 1

    async def click(self, *, timeout: int) -> None:
        self._page.clicked_count += 1
        self._page.page_index = min(self._page.page_index + 1, len(self._page.pages) - 1)

    async def all_text_contents(self) -> list[str | None]:
        if self._selector == ".row":
            return self._page.pages[self._page.page_index]
        return []

    async def evaluate_all(self, script: str, attribute: str | None = None) -> list[str | None]:
        return await self.all_text_contents()


class FakeUnionPaginatePage(FakePaginatePage):
    """两个分页控件都在：`a.next_page` 前进一页，`a.page_normal` 是回到第 2 页的页码链接。"""

    def __init__(self) -> None:
        super().__init__()
        self.clicked_selectors: list[str] = []

    def locator(self, selector: str) -> "FakeUnionPaginateLocator":
        return FakeUnionPaginateLocator(self, selector)


class FakeUnionPaginateLocator(FakePaginateLocator):
    async def count(self) -> int:
        return 1

    async def is_disabled(self) -> bool:
        return self._selector == "a.next_page" and self._page.page_index >= len(self._page.pages) - 1

    async def click(self, *, timeout: int) -> None:
        self._page.clicked_selectors.append(self._selector)
        if self._selector == "a.next_page":
            self._page.page_index = min(self._page.page_index + 1, len(self._page.pages) - 1)
        else:
            self._page.page_index = 1

    async def all_text_contents(self) -> list[str | None]:
        if self._selector == ".row":
            return self._page.pages[self._page.page_index]
        return []


class FakeUnmatchedPaginatePage(FakePaginatePage):
    """分页控件存在，但节点配的 selector 一个都没匹配上。"""

    def locator(self, selector: str) -> "FakeUnmatchedPaginateLocator":
        return FakeUnmatchedPaginateLocator(self, selector)


class FakeUnmatchedPaginateLocator(FakePaginateLocator):
    async def count(self) -> int:
        return 0 if self._selector == "a.normal_page_right" else len(self._page.pages)

    async def is_visible(self) -> bool:
        return self._selector != "a.normal_page_right"


class FakeDismissPage:
    def __init__(self) -> None:
        self.clicked_selectors: list[str] = []
        self.waited_selectors: list[str] = []
        self.waited_timeouts: list[int] = []

    def locator(self, selector: str) -> "FakeDismissLocator":
        return FakeDismissLocator(self, selector)

    async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
        self.waited_selectors.append(selector)

    async def wait_for_timeout(self, delay_ms: int) -> None:
        self.waited_timeouts.append(delay_ms)


class FakeDismissLocator:
    def __init__(self, page: FakeDismissPage, selector: str) -> None:
        self._page = page
        self._selector = selector

    def first(self) -> "FakeDismissLocator":
        return self

    async def wait_for(self, *, state: str, timeout: int) -> None:
        self._page.waited_selectors.append(self._selector)

    async def is_visible(self) -> bool:
        return self._selector == "button.accept-cookie"

    async def is_disabled(self) -> bool:
        return False

    async def click(self, *, timeout: int) -> None:
        self._page.clicked_selectors.append(self._selector)


def test_table_scope_error_no_rows_raises_instead_of_returning_empty() -> None:
    """table 模式选中的元素里一行都没有时必须失败，而不是返回空列表。"""
    import pytest

    with pytest.raises(RuntimeError, match="没有任何表格行"):
        _raise_if_table_scope_error({"__table_scope_error": "no_rows_in_scope"}, ".stats-card")


def test_table_scope_error_multiple_tables_raises_with_count() -> None:
    """selector 圈到多张数据表时必须失败。"""
    import pytest

    with pytest.raises(RuntimeError, match="3 张不同的表格"):
        _raise_if_table_scope_error(
            {"__table_scope_error": "multiple_tables_in_scope", "tableCount": 3},
            ".workbench-page",
        )


def test_table_scope_error_passes_through_normal_rows() -> None:
    _raise_if_table_scope_error([["a", "b"], ["c", "d"]], "tbody tr")


class _FakePage:
    """记录每次 goto 的超时值，按 outcomes 依次决定抛错还是成功。"""

    def __init__(self, outcomes: list[Exception | None]) -> None:
        self._outcomes = outcomes
        self.timeouts: list[int] = []

    async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.timeouts.append(timeout)
        outcome = self._outcomes.pop(0)
        if outcome is not None:
            raise outcome


async def test_navigation_retries_once_after_a_fast_first_timeout() -> None:
    """首次连接抖动不该让整轮流程失败：先 15s 快速失败，再按完整超时重试。"""
    page = _FakePage([TimeoutError("Page.goto: Timeout 15000ms exceeded"), None])

    await _goto_with_retry(page, "https://example.com", timeout=30_000)

    assert page.timeouts == [15_000, 30_000]


async def test_navigation_does_not_retry_non_timeout_failures() -> None:
    """证书/无效 URL 这类错误重试一遍结果一样，只会多等一次。"""
    import pytest

    page = _FakePage([RuntimeError("net::ERR_CERT_AUTHORITY_INVALID")])

    with pytest.raises(RuntimeError, match="ERR_CERT_AUTHORITY_INVALID"):
        await _goto_with_retry(page, "https://example.com", timeout=30_000)

    assert page.timeouts == [15_000]
