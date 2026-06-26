from __future__ import annotations

from app.services.browser_action_runner import BrowserActionContext, BrowserActionResult, BrowserActionRunner, apply_browser_result_variables
from app.services.runtime_variables import RuntimeVariableStore


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
    assert result.detail == "button.load-more -> .product-card"
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
    assert result.detail == "a.next -> .row"
    assert result.values == ["A1", "A2", "B1", "B2", "C1"]
    assert variables.get("visited_pages") == 3


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


class FakePage:
    def __init__(self, values: list[str | None]) -> None:
        self._values = values
        self.waited_selector: str | None = None
        self.locator_selector: str | None = None
        self.attribute: str | None = None
        self.used_dom_property_lookup = False
        self.used_inner_html_lookup = False

    async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
        self.waited_selector = selector

    def locator(self, selector: str) -> "FakePage":
        self.locator_selector = selector
        return self

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

    async def press(self, selector: str, key: str, *, timeout: int) -> None:
        self.pressed.append((selector, key, timeout))

    def locator(self, selector: str) -> "FakePressPage":
        return self


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

    async def is_visible(self) -> bool:
        return self._selector == "button.accept-cookie"

    async def is_disabled(self) -> bool:
        return False

    async def click(self, *, timeout: int) -> None:
        self._page.clicked_selectors.append(self._selector)
