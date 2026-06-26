from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.models.schemas import ScrapeResult
from app.services.runtime_variables import RuntimeVariableStore, append_variable_values, normalize_variable_name

type FlowNode = dict[str, object]

_BROWSER_ACTION_NODE_TYPES = {
    "browser.open",
    "browser.click",
    "browser.fill",
    "browser.press",
    "browser.wait",
    "browser.extract",
    "browser.dismiss",
    "browser.clickLoadMore",
    "browser.paginateNext",
    "browser.screenshot",
    "browser.scroll",
    "browser.select",
    "browser.check",
    "browser.hover",
    "browser.drag",
    "browser.tab.open",
    "browser.tab.close",
    "browser.tab.switch",
    "ui.click",
    "ui.fill",
    "ui.wait",
    "ui.extract",
    "ui.screenshot",
    "ui.select",
    "ui.check",
    "ui.drag",
}
_MAX_TEXT_VALUES = 200
_URL_ATTRIBUTE_NAMES = {"href", "src", "action", "poster"}
_SELECTOR_ATTRIBUTE_PATTERN = re.compile(r"^(?P<selector>.+?)::attr\((?P<attribute>[A-Za-z_][A-Za-z0-9_:-]{0,63})\)\s*$")


@dataclass
class BrowserActionContext:
    playwright: object
    browser: object
    page: object
    # True when `browser` is a persistent BrowserContext (launch_persistent_context)
    persistent: bool = field(default=False)


@dataclass(frozen=True)
class BrowserActionResult:
    action_type: str
    detail: str
    # Display/summary strings — feed the Pydantic ScrapeResult (list[str]).
    values: list[str]
    # Optional real structured rows (list[dict] | list[list]) from table extraction.
    # When present these are what the output variable receives, so downstream
    # file.write / excel / JSON serialize cleanly instead of double-encoding.
    structured: list[object] | None = None

    @property
    def rows(self) -> list[object]:
        """Structured rows when available, otherwise the display strings."""
        return self.structured if self.structured is not None else list(self.values)

    def to_scrape_result(self) -> ScrapeResult:
        return ScrapeResult(url=self.detail, selector=self.action_type, count=len(self.values), values=self.values)


class BrowserActionRunner:
    def __init__(self, session_dir: str | None = None) -> None:
        self._session_dir = session_dir

    async def create_context(self) -> BrowserActionContext:
        try:
            from playwright.async_api import async_playwright
        except ModuleNotFoundError as exc:
            raise RuntimeError("未安装 Playwright，请执行 uv pip install playwright") from exc

        playwright = await async_playwright().start()

        if self._session_dir is not None:
            profile_path = Path(self._session_dir)
            profile_path.mkdir(parents=True, exist_ok=True)
            # launch_persistent_context keeps cookies/localStorage across runs.
            # --disable-cache disables the HTTP disk cache so the RPA always fetches
            # the latest page content, while cookies and localStorage are preserved.
            browser_context = await playwright.chromium.launch_persistent_context(
                str(profile_path),
                headless=True,
                args=['--disable-cache'],
            )
            page = await browser_context.new_page()
            return BrowserActionContext(playwright=playwright, browser=browser_context, page=page, persistent=True)

        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        return BrowserActionContext(playwright=playwright, browser=browser, page=page)

    async def close_context(self, context: BrowserActionContext | None) -> None:
        if context is None:
            return
        close = getattr(context.browser, "close", None)
        stop = getattr(context.playwright, "stop", None)
        if callable(close):
            await close()
        if callable(stop):
            await stop()

    async def screenshot(self, context: BrowserActionContext) -> bytes:
        return await context.page.screenshot(full_page=True, type="png")

    async def run(self, node: FlowNode, variables: RuntimeVariableStore, context: BrowserActionContext, *, timeout_ms: int) -> BrowserActionResult:
        action_type = _normalize_action_type(_read_action_type(node))
        timeout = max(1, timeout_ms)
        page = context.page

        delay_ms = _read_int(node, "delayMs", default=0)
        compound_delay_actions = {"browser.clickLoadMore", "browser.paginateNext", "browser.dismiss"}
        if delay_ms > 0 and action_type not in compound_delay_actions:
            await page.wait_for_timeout(delay_ms)

        if action_type == "browser.open":
            url = variables.resolve_text(_read_required_string(node, "targetUrl"))
            clear_storage = _read_bool(node, "clearStorage", default=False)
            clear_cookies = _read_bool(node, "clearCookies", default=False)

            if clear_cookies:
                await page.context.clear_cookies()

            # clearStorage: navigate first to establish origin, then wipe localStorage /
            # sessionStorage so a stale auth token can't block SPA initialization,
            # then reload so the app starts clean (will see no token → redirect to login).
            if clear_storage:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                await page.evaluate("localStorage.clear(); sessionStorage.clear();")
                await page.reload(wait_until="domcontentloaded", timeout=timeout)
            else:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout)

            return BrowserActionResult(action_type=action_type, detail=page.url, values=[page.url])

        if action_type == "browser.tab.open":
            url = _read_optional_string(node, "targetUrl")
            new_page = await context.page.context.new_page()
            if url is not None:
                target_url = variables.resolve_text(url)
                await new_page.goto(target_url, wait_until="domcontentloaded", timeout=timeout)
            context.page = new_page
            return BrowserActionResult(action_type=action_type, detail=new_page.url, values=[new_page.url])

        if action_type == "browser.tab.switch":
            index = _read_non_negative_int(node, "index", default=0)
            pages = context.page.context.pages
            if index >= len(pages):
                raise ValueError("标签页索引超出范围")
            context.page = pages[index]
            await context.page.bring_to_front()
            return BrowserActionResult(action_type=action_type, detail=context.page.url, values=[context.page.url])

        if action_type == "browser.tab.close":
            current_page = context.page
            pages = current_page.context.pages
            if len(pages) <= 1:
                return BrowserActionResult(action_type=action_type, detail=current_page.url, values=[current_page.url])
            await current_page.close()
            context.page = next((item for item in pages if not item.is_closed()), pages[0])
            return BrowserActionResult(action_type=action_type, detail=context.page.url, values=[context.page.url])

        if action_type == "browser.scroll":
            distance = _read_int(node, "distance", default=800)
            await page.mouse.wheel(0, distance)
            return BrowserActionResult(action_type=action_type, detail=str(distance), values=[str(distance)])

        if action_type == "browser.screenshot":
            return BrowserActionResult(action_type=action_type, detail=page.url, values=[page.url])

        selector_config = _read_selector_config(node, variables)
        selector = selector_config.selector

        async def _enrich_selector_error(exc: Exception, sel: str) -> None:
            """Append element count to the exception message so get_run_error carries actionable info."""
            try:
                count = await page.locator(sel).count()
                base_message = str(exc.args[0]) if exc.args else str(exc)
                exc.args = (f"{base_message} [selector '{sel}' 页面匹配 {count} 个元素]", *exc.args[1:])
            except Exception:
                pass

        if action_type == "browser.click":
            try:
                await _click_first_visible(page, selector, timeout=timeout)
            except Exception as exc:
                await _enrich_selector_error(exc, selector)
                raise
            return BrowserActionResult(action_type=action_type, detail=selector, values=[selector])

        if action_type == "browser.fill":
            input_value = variables.resolve_text(_read_required_string(node, "inputValue", fallback_keys=("value",)))
            fill_mode = _read_optional_string(node, "fillMode") or "fill"
            if fill_mode == "js":
                try:
                    await page.wait_for_selector(selector, timeout=timeout)
                except Exception as exc:
                    await _enrich_selector_error(exc, selector)
                    raise
                await page.evaluate(
                    """([sel, val]) => {
                        const el = document.querySelector(sel);
                        if (!el) return;
                        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                        if (nativeInputValueSetter) { nativeInputValueSetter.call(el, val); }
                        else { el.value = val; }
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    [selector, input_value],
                )
            elif fill_mode == "type":
                try:
                    await page.click(selector, timeout=timeout)
                except Exception as exc:
                    await _enrich_selector_error(exc, selector)
                    raise
                try:
                    await page.keyboard.press("ControlOrMeta+A")
                    await page.keyboard.press("Backspace")
                    await page.keyboard.type(input_value)
                    await page.evaluate(
                        """() => {
                            const el = document.activeElement;
                            if (!el) return;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Process' }));
                        }"""
                    )
                except Exception as exc:
                    await _enrich_selector_error(exc, selector)
                    raise
            else:
                try:
                    await page.fill(selector, input_value, timeout=timeout)
                except Exception as exc:
                    await _enrich_selector_error(exc, selector)
                    raise
            return BrowserActionResult(action_type=action_type, detail=selector, values=[input_value])

        if action_type == "browser.press":
            key = variables.resolve_text(_read_required_string(node, "inputValue", fallback_keys=("key", "value")))
            if _is_page_level_press_selector(selector):
                # Element UI/Ant Design 这类弹层常会在选择后重建输入框。
                # 对 Escape 等全局按键直接发给页面，避免依赖已不稳定的业务控件。
                await page.keyboard.press(key)
            else:
                try:
                    await page.press(selector, key, timeout=timeout)
                except Exception as exc:
                    await _enrich_selector_error(exc, selector)
                    raise
            return BrowserActionResult(action_type=action_type, detail=selector, values=[key])

        if action_type == "browser.wait":
            try:
                await page.wait_for_selector(selector, timeout=timeout)
            except Exception as exc:
                await _enrich_selector_error(exc, selector)
                raise
            return BrowserActionResult(action_type=action_type, detail=selector, values=[selector])

        if action_type == "browser.dismiss":
            dismissed = await self._dismiss_overlays(page, selector, variables, node, timeout=timeout)
            return BrowserActionResult(action_type=action_type, detail=selector, values=[str(dismissed)])

        if action_type == "browser.extract":
            rows = await _extract_locator_values(page, selector_config, timeout=timeout)
            return _build_extract_result(action_type, selector, rows)

        if action_type == "browser.clickLoadMore":
            target_selector_config = _read_target_selector_config(node, variables)
            rows = await self._click_load_more_and_extract(page, selector, target_selector_config, variables, node, timeout=timeout)
            return _build_extract_result(action_type, f"{selector} -> {target_selector_config.selector}", rows)

        if action_type == "browser.paginateNext":
            target_selector_config = _read_target_selector_config(node, variables)
            rows = await self._paginate_next_and_extract(page, selector, target_selector_config, variables, node, timeout=timeout)
            return _build_extract_result(action_type, f"{selector} -> {target_selector_config.selector}", rows)

        if action_type == "browser.select":
            input_value = variables.resolve_text(_read_required_string(node, "inputValue", fallback_keys=("value",)))
            try:
                selected = await page.select_option(selector, input_value, timeout=timeout)
            except Exception as exc:
                await _enrich_selector_error(exc, selector)
                raise
            return BrowserActionResult(action_type=action_type, detail=selector, values=selected)

        if action_type == "browser.check":
            checked = _read_bool(node, "checked", default=True)
            try:
                if checked:
                    await page.check(selector, timeout=timeout)
                else:
                    await page.uncheck(selector, timeout=timeout)
            except Exception as exc:
                await _enrich_selector_error(exc, selector)
                raise
            return BrowserActionResult(action_type=action_type, detail=selector, values=[str(checked).lower()])

        if action_type == "browser.hover":
            try:
                await page.hover(selector, timeout=timeout)
            except Exception as exc:
                await _enrich_selector_error(exc, selector)
                raise
            delay_ms = max(0, _read_int(node, "delayMs", default=0))
            if delay_ms > 0:
                import asyncio as _asyncio
                await _asyncio.sleep(delay_ms / 1000)
            return BrowserActionResult(action_type=action_type, detail=selector, values=[selector])

        if action_type == "browser.drag":
            target_selector = variables.resolve_text(_read_required_string(node, "targetSelector", fallback_keys=("target",)))
            try:
                await page.drag_and_drop(selector, target_selector, timeout=timeout)
            except Exception as exc:
                await _enrich_selector_error(exc, selector)
                raise
            return BrowserActionResult(action_type=action_type, detail=f"{selector} -> {target_selector}", values=[target_selector])

        raise ValueError(f"不支持的浏览器动作类型: {action_type}")

    async def _click_load_more_and_extract(
        self,
        page: object,
        button_selector: str,
        target_selector_config: "SelectorConfig",
        variables: RuntimeVariableStore,
        node: FlowNode,
        *,
        timeout: int,
    ) -> list[object]:
        max_iterations = max(1, _read_int(node, "maxIterations", default=5))
        delay_ms = max(0, _read_int(node, "delayMs", default=500))
        previous_count = await _count_locator(page, target_selector_config.selector)

        for _index in range(max_iterations):
            button = _first_locator(page.locator(button_selector))
            if await _is_locator_hidden(button):
                break
            await button.click(timeout=timeout)
            if delay_ms > 0:
                await page.wait_for_timeout(delay_ms)
            next_count = await _count_locator(page, target_selector_config.selector)
            if next_count <= previous_count:
                break
            previous_count = next_count

        count_variable = _read_optional_string(node, "loadedCountVariable")
        if count_variable is not None:
            variables.set(count_variable, previous_count, scope="局部")
        return await _extract_locator_values(page, target_selector_config, timeout=timeout)

    async def _paginate_next_and_extract(
        self,
        page: object,
        next_selector: str,
        target_selector_config: "SelectorConfig",
        variables: RuntimeVariableStore,
        node: FlowNode,
        *,
        timeout: int,
    ) -> list[object]:
        max_iterations = max(1, _read_int(node, "maxIterations", default=20))
        delay_ms = max(0, _read_int(node, "delayMs", default=500))
        pages_visited = 0
        all_values: list[object] = []
        previous_fingerprint = ""

        for _index in range(max_iterations):
            current_values = await _extract_locator_values(page, target_selector_config, timeout=timeout)
            all_values.extend(current_values)
            pages_visited += 1

            next_button = _first_locator(page.locator(next_selector))
            if await _is_locator_hidden(next_button) or await _is_locator_disabled(next_button):
                break

            before_url = _read_page_url(page)
            before_fingerprint = _build_page_fingerprint(before_url, current_values)
            await next_button.click(timeout=timeout)
            if delay_ms > 0:
                await page.wait_for_timeout(delay_ms)
            after_values = await _extract_locator_values(page, target_selector_config, timeout=timeout)
            after_url = _read_page_url(page)
            after_fingerprint = _build_page_fingerprint(after_url, after_values)
            if after_fingerprint == before_fingerprint or after_fingerprint == previous_fingerprint:
                break
            previous_fingerprint = before_fingerprint

        pages_variable = _read_optional_string(node, "pageCountVariable")
        if pages_variable is not None:
            variables.set(pages_variable, pages_visited, scope="局部")
        return all_values

    async def _dismiss_overlays(self, page: object, selector: str, variables: RuntimeVariableStore, node: FlowNode, *, timeout: int) -> int:
        selectors = _split_selector_candidates(selector)
        delay_ms = max(0, _read_int(node, "delayMs", default=200))
        max_iterations = max(1, _read_int(node, "maxIterations", default=len(selectors) or 1))
        dismissed = 0

        for candidate in selectors[:max_iterations]:
            locator = _first_locator(page.locator(candidate))
            if await _is_locator_hidden(locator) or await _is_locator_disabled(locator):
                continue
            await locator.click(timeout=timeout)
            dismissed += 1
            if delay_ms > 0:
                await page.wait_for_timeout(delay_ms)

        target_selector = _read_optional_string(node, "targetSelector")
        if target_selector is not None:
            resolved_target = variables.resolve_text(target_selector)
            await page.wait_for_selector(resolved_target, timeout=timeout)

        count_variable = _read_optional_string(node, "dismissedCountVariable")
        if count_variable is not None:
            variables.set(count_variable, dismissed, scope="局部")
        return dismissed


def is_browser_action_node(node: FlowNode) -> bool:
    return node.get("type") in _BROWSER_ACTION_NODE_TYPES


def apply_browser_result_variables(node: FlowNode, result: BrowserActionResult, variables: RuntimeVariableStore) -> list[str]:
    saved_names: list[str] = []

    rows = result.rows

    output_variable = _read_optional_string(node, "outputVariable") or _read_optional_string(node, "responseVariable") or _read_optional_string(node, "resultVariable")
    if output_variable is not None:
        variables.set(output_variable, rows, scope="局部")
        saved_names.append(output_variable)

    first_value_variable = _read_optional_string(node, "firstValueVariable")
    if first_value_variable is not None:
        variables.set(first_value_variable, rows[0] if rows else "", scope="局部")
        saved_names.append(first_value_variable)

    count_variable = _read_optional_string(node, "countVariable")
    if count_variable is not None:
        count_value: object = len(result.values)
        if node.get("extractMode") == "count" and result.values:
            raw_count = result.values[0]
            if isinstance(raw_count, str) and raw_count.strip().isdigit():
                count_value = int(raw_count.strip())
            elif isinstance(raw_count, int) and not isinstance(raw_count, bool):
                count_value = raw_count
        variables.set(count_variable, count_value, scope="局部")
        saved_names.append(count_variable)

    append_variable = _read_optional_string(node, "appendVariable") or _read_optional_string(node, "appendOutputVariable")
    if append_variable is not None:
        if node.get("appendMode") == "record":
            append_variable_values(
                variables,
                append_variable,
                [
                    {
                        "count": len(rows),
                        "detail": result.detail,
                        "first": rows[0] if rows else "",
                        "values": rows,
                    }
                ],
            )
        else:
            append_variable_values(variables, append_variable, list(rows))
        saved_names.append(normalize_variable_name(append_variable))

    return saved_names


def _read_action_type(node: FlowNode) -> str:
    value = node.get("type")
    if not isinstance(value, str):
        raise ValueError("浏览器动作节点缺少 type")
    return value


def _normalize_action_type(action_type: str) -> str:
    ui_aliases = {
        "ui.click": "browser.click",
        "ui.fill": "browser.fill",
        "ui.wait": "browser.wait",
        "ui.extract": "browser.extract",
        "ui.screenshot": "browser.screenshot",
        "ui.select": "browser.select",
        "ui.check": "browser.check",
        "ui.drag": "browser.drag",
    }
    return ui_aliases.get(action_type, action_type)


def _read_required_string(node: FlowNode, key: str, *, fallback_keys: tuple[str, ...] = ()) -> str:
    for candidate_key in (key, *fallback_keys):
        value = node.get(candidate_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"浏览器动作节点缺少 {key}")


def _read_optional_string(node: FlowNode, key: str) -> str | None:
    value = node.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _read_int(node: FlowNode, key: str, *, default: int) -> int:
    value = node.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _read_non_negative_int(node: FlowNode, key: str, *, default: int) -> int:
    return max(0, _read_int(node, key, default=default))


def _read_bool(node: FlowNode, key: str, *, default: bool) -> bool:
    value = node.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "是"}:
            return True
        if normalized in {"false", "0", "no", "否"}:
            return False
    return default


@dataclass(frozen=True)
class SelectorConfig:
    selector: str
    extract_mode: str
    attribute: str | None = None


def _read_selector_config(node: FlowNode, variables: RuntimeVariableStore) -> SelectorConfig:
    raw_selector = variables.resolve_text(_read_required_string(node, "selector"))
    selector = raw_selector.strip()
    extract_mode = _read_optional_string(node, "extractMode") or "text"
    attribute = _read_optional_string(node, "attribute")

    attribute_match = _SELECTOR_ATTRIBUTE_PATTERN.match(selector)
    if attribute_match is not None:
        return SelectorConfig(selector=attribute_match.group("selector").strip(), extract_mode="attribute", attribute=attribute_match.group("attribute"))

    if selector.endswith("::text"):
        return SelectorConfig(selector=selector[: -len("::text")].strip(), extract_mode="text", attribute=attribute)

    return SelectorConfig(selector=selector, extract_mode=extract_mode, attribute=attribute)


def _is_page_level_press_selector(selector: str) -> bool:
    return selector.strip().lower() in {"body", "html", "document", "window", ":root", "page"}


def _read_target_selector_config(node: FlowNode, variables: RuntimeVariableStore) -> SelectorConfig:
    raw_selector = variables.resolve_text(_read_required_string(node, "targetSelector", fallback_keys=("itemSelector", "extractSelector")))
    target_node = {
        "selector": raw_selector,
        "extractMode": _read_optional_string(node, "extractMode") or "text",
        "attribute": _read_optional_string(node, "attribute"),
    }
    return _read_selector_config(target_node, variables)


def _split_selector_candidates(value: str) -> list[str]:
    selectors = [item.strip() for item in value.splitlines() if item.strip()]
    if selectors:
        return selectors
    return [value.strip()] if value.strip() else []


async def _extract_locator_values(page: object, selector_config: SelectorConfig, *, timeout: int) -> list[object]:
    await page.wait_for_selector(selector_config.selector, timeout=timeout)
    locator = page.locator(selector_config.selector)
    if selector_config.extract_mode == "count":
        raw_values = [str(await _count_locator(page, selector_config.selector))]
    elif selector_config.extract_mode == "attribute":
        attribute = selector_config.attribute or "href"
        raw_values = await locator.evaluate_all(
            _build_attribute_extract_script(),
            attribute,
        )
    elif selector_config.extract_mode == "html":
        raw_values = await locator.evaluate_all("(elements) => elements.map((element) => element.innerHTML)")
    elif selector_config.extract_mode == "table":
        # Generic, page-agnostic table extraction. Returns structured rows:
        #   - list[dict] keyed by the table's auto-detected column headers, or
        #   - list[list] of cell texts when no usable header row is found.
        # Storing real objects (not pre-stringified JSON) lets downstream
        # file.write / excel / JSON output serialize cleanly via the normal
        # path — no per-flow header node, labeling script, or cleaning script.
        # Works for plain <table>, Element UI, Ant Design, etc.; degenerate
        # fixed-column shadow rows are dropped, and small tables are untouched.
        raw_rows = await locator.evaluate_all(_TABLE_EXTRACT_SCRIPT)
        return _normalize_table_rows(raw_rows)
    else:
        raw_values = await locator.all_text_contents()
    return _clean_text_values(raw_values)


# Runs once over ALL matched row elements. Detects headers from the nearest
# table's thead (or a homogeneous first th-row), drops degenerate shadow rows
# (fewer than half the dominant column count), and emits header-labeled objects
# when a usable header row is found, otherwise positional cell arrays.
_TABLE_EXTRACT_SCRIPT = (
    "(elements) => {"
    " if (!elements.length) return [];"
    " const txt = (c) => (c && c.innerText ? c.innerText : '').replace(/\\s+/g, ' ').trim();"
    " const colNo = (el) => {"
    "   const cls = String(el.className || '');"
    "   const m = cls.match(/(?:^|\\s)el-table_\\d+_column_(\\d+)(?:\\s|$)/);"
    "   return m ? Number(m[1]) : null;"
    " };"
    " const table = elements[0].closest('table');"
    " const root = table ? (table.closest('.el-table') || table.closest('[role=grid]') || table.parentElement) : null;"
    " let headerPairs = [];"
    " if (root) {"
    "   const ths = root.querySelectorAll('thead th');"
    "   headerPairs = Array.from(ths).map((th, i) => ({"
    "     index: i + 1,"
    "     col: colNo(th),"
    "     text: txt(th.querySelector('.cell') || th)"
    "   })).filter((h) => h.text !== '');"
    " }"
    " if (!headerPairs.length && table) {"
    "   const ths = table.querySelectorAll('thead th');"
    "   headerPairs = Array.from(ths).map((th, i) => ({ index: i + 1, col: colNo(th), text: txt(th.querySelector('.cell') || th) })).filter((h) => h.text !== '');"
    " }"
    " const headers = headerPairs.map((h) => h.text);"
    " const headerByCol = new Map(headerPairs.filter((h) => h.col).map((h) => [h.col, h.text]));"
    " const cellsOf = (row) => {"
    "   const cells = Array.from(row.querySelectorAll('td, th')).map((cell, i) => ({"
    "     index: i + 1,"
    "     col: colNo(cell),"
    "     text: txt(cell.querySelector('.cell') || cell)"
    "   })).filter((c) => c.text !== '');"
    "   const hasColumnClasses = cells.some((c) => c.col) && headerByCol.size >= 2;"
    "   if (!hasColumnClasses) return cells.map((c) => c.text);"
    "   const obj = {};"
    "   cells.forEach((c) => {"
    "     const key = headerByCol.get(c.col) || headers[c.index - 1] || ('col_' + c.index);"
    "     obj[key] = c.text;"
    "   });"
    "   return obj;"
    " };"
    " const rows = elements.map(cellsOf);"
    " const widths = rows.map((r) => Array.isArray(r) ? r.length : Object.keys(r).length).filter((n) => n > 0);"
    " const maxW = widths.length ? Math.max.apply(null, widths) : 0;"
    " const threshold = maxW >= 3 ? Math.ceil(maxW / 2) : 1;"
    " const clean = rows.filter((r) => (Array.isArray(r) ? r.length : Object.keys(r).length) >= threshold);"
    " const useHeaders = headers.length >= Math.max(2, Math.floor(maxW * 0.6)) ? headers : null;"
    " return clean.map((r) => {"
    "   if (!Array.isArray(r)) return r;"
    "   if (!useHeaders) return r;"
    "   const obj = {};"
    "   r.forEach((v, i) => { obj[useHeaders[i] || ('col_' + (i + 1))] = v; });"
    "   return obj;"
    " });"
    "}"
)


def _build_extract_result(action_type: str, detail: str, rows: list[object]) -> "BrowserActionResult":
    """Split extracted rows into display strings (for the SSE/summary contract)
    and structured rows (for the output variable). Plain text/attribute/html
    extraction stays string-only; table extraction carries real objects through.
    """
    has_structured = any(isinstance(row, (dict, list)) for row in rows)
    if has_structured:
        values = [row if isinstance(row, str) else json.dumps(row, ensure_ascii=False) for row in rows]
        return BrowserActionResult(action_type=action_type, detail=detail, values=values, structured=list(rows))
    return BrowserActionResult(action_type=action_type, detail=detail, values=[str(row) for row in rows])


def _normalize_table_rows(raw_rows: object) -> list[object]:
    """Pass structured table rows through, dropping empties; tolerate odd shapes."""
    if not isinstance(raw_rows, list):
        return []
    rows: list[object] = []
    for row in raw_rows:
        if isinstance(row, dict):
            if any(str(v).strip() for v in row.values()):
                rows.append(row)
        elif isinstance(row, list):
            cells = [str(c).strip() for c in row if str(c).strip()]
            if cells:
                rows.append(cells)
        elif str(row).strip():
            rows.append(str(row).strip())
    return rows


async def _count_locator(page: object, selector: str) -> int:
    locator = page.locator(selector)
    count = getattr(locator, "count", None)
    if not callable(count):
        return 0
    value = await count()
    return value if isinstance(value, int) else 0


async def _click_first_visible(page: object, selector: str, *, timeout: int) -> None:
    locator = page.locator(selector)
    count = await _count_locator(page, selector)
    if count <= 1:
        await page.click(selector, timeout=timeout)
        return

    for index in range(count):
        item = locator.nth(index)
        try:
            if await _is_locator_hidden(item) or await _is_locator_disabled(item):
                continue
            await item.click(timeout=timeout)
            return
        except Exception:
            continue

    # 保留 Playwright 原生错误信息，便于上层附加 selector 命中数。
    await page.click(selector, timeout=timeout)


def _first_locator(locator: object) -> object:
    first = getattr(locator, "first", None)
    return first() if callable(first) else first if first is not None else locator


async def _is_locator_hidden(locator: object) -> bool:
    is_visible = getattr(locator, "is_visible", None)
    if not callable(is_visible):
        return False
    return not bool(await is_visible())


async def _is_locator_disabled(locator: object) -> bool:
    is_disabled = getattr(locator, "is_disabled", None)
    if callable(is_disabled):
        return bool(await is_disabled())
    get_attribute = getattr(locator, "get_attribute", None)
    if callable(get_attribute):
        aria_disabled = await get_attribute("aria-disabled")
        disabled = await get_attribute("disabled")
        return str(aria_disabled).lower() == "true" or disabled is not None
    return False


def _read_page_url(page: object) -> str:
    value = getattr(page, "url", "")
    return value if isinstance(value, str) else ""


def _build_page_fingerprint(url: str, values: list[str]) -> str:
    first = values[0] if values else ""
    last = values[-1] if values else ""
    return f"{url}|{len(values)}|{first}|{last}"


def _clean_text_values(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for value in values[:_MAX_TEXT_VALUES]:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized:
            cleaned.append(normalized)
    return cleaned


def _build_attribute_extract_script() -> str:
    url_attributes = json.dumps(sorted(_URL_ATTRIBUTE_NAMES))
    return f"""
    (elements, attribute) => elements
      .map((element) => {{
        if (attribute === 'value' && 'value' in element) {{
          return element.value;
        }}
        if ({url_attributes}.includes(attribute) && attribute in element) {{
          return element[attribute];
        }}
        return element.getAttribute(attribute);
      }})
      .filter((value) => value !== null && value !== undefined)
    """
