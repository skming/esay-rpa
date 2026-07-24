from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.models.schemas import ScrapeResult
from app.services.runtime_variables import RuntimeVariableStore, append_variable_values, normalize_variable_name

type FlowNode = dict[str, object]

_BROWSER_ACTION_NODE_TYPES = {
    "browser.open",
    "browser.ensureLogin",
    "browser.click",
    "browser.fill",
    "browser.press",
    "browser.wait",
    "browser.waitFor",
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
_MAX_TEXT_VALUES = 200  # 提取结果展示字符串数量上限，防止超大页面把变量/日志撑爆
_URL_ATTRIBUTE_NAMES = {"href", "src", "action", "poster"}
_NAV_FIRST_ATTEMPT_TIMEOUT_MS = 15_000  # 首次导航的快速失败阈值
_NAV_RETRYABLE_ERROR = re.compile(
    r"timeout|ERR_CONNECTION|ERR_TIMED_OUT|ERR_NETWORK|ERR_EMPTY_RESPONSE|ERR_SOCKET",
    re.IGNORECASE,
)
_SELECTOR_ATTRIBUTE_PATTERN = re.compile(r"^(?P<selector>.+?)::attr\((?P<attribute>[A-Za-z_][A-Za-z0-9_:-]{0,63})\)\s*$")


@dataclass
class BrowserActionContext:
    playwright: object
    browser: object
    page: object
    # browser 是否为 launch_persistent_context 创建的持久化 BrowserContext。
    persistent: bool = field(default=False)
    # 无头模式下窗口不可见，无法人工接管。
    headless: bool = field(default=True)


@dataclass(frozen=True)
class BrowserActionResult:
    action_type: str
    detail: str
    # 展示/摘要用字符串，供 ScrapeResult(list[str]) 使用。
    values: list[str]
    # 表格抓取产生的结构化行（list[dict] | list[list]）；存在时输出变量取这个，
    # 使下游 file.write / excel / JSON 能直接序列化而不是二次编码。
    structured: list[object] | None = None

    @property
    def rows(self) -> list[object]:
        """Structured rows when available, otherwise the display strings."""
        return self.structured if self.structured is not None else list(self.values)

    def to_scrape_result(self) -> ScrapeResult:
        return ScrapeResult(
            url=self.detail,
            selector=self.action_type,
            count=len(self.values),
            values=self.values,
            structured=self.structured,
        )


async def _goto_with_retry(page: object, url: str, *, timeout: int) -> None:
    """首次 15s 快速失败后重试一次，仍失败才抛出。

    单次 30s 超时会让一次首连抖动直接判整轮流程失败，助手再从头重跑。
    非超时类错误（证书、无效 URL）不重试，重试结果一样只是多等一次。
    """
    first_timeout = min(timeout, _NAV_FIRST_ATTEMPT_TIMEOUT_MS)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=first_timeout)
        return
    except Exception as exc:
        if not _NAV_RETRYABLE_ERROR.search(str(exc)):
            raise
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout)


class BrowserActionRunner:
    def __init__(self, session_dir: str | None = None) -> None:
        self._session_dir = session_dir

    async def create_context(self, *, headless: bool = True) -> BrowserActionContext:
        try:
            from playwright.async_api import async_playwright
        except ModuleNotFoundError as exc:
            raise RuntimeError("未安装 Playwright，请执行 uv pip install playwright") from exc

        playwright = await async_playwright().start()

        if self._session_dir is not None:
            profile_path = Path(self._session_dir)
            profile_path.mkdir(parents=True, exist_ok=True)
            # launch_persistent_context 保留跨运行的 cookies/localStorage；
            # --disable-cache 禁用 HTTP 磁盘缓存，确保每次都取到最新页面内容。
            browser_context = await playwright.chromium.launch_persistent_context(
                str(profile_path),
                headless=headless,
                args=['--disable-cache'],
            )
            page = await browser_context.new_page()
            return BrowserActionContext(playwright=playwright, browser=browser_context, page=page, persistent=True, headless=headless)

        browser = await playwright.chromium.launch(headless=headless)
        page = await browser.new_page()
        return BrowserActionContext(playwright=playwright, browser=browser, page=page, headless=headless)

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
        try:
            return await self._run_action(node, variables, context, timeout_ms=timeout_ms)
        except Exception:
            healed = await _heal_selector(context.page, node, variables)
            if healed is None:
                raise
            healed_node = dict(node)
            healed_node["selector"] = healed
            result = await self._run_action(healed_node, variables, context, timeout_ms=timeout_ms)
            return BrowserActionResult(
                action_type=result.action_type,
                detail=f"{result.detail}（selector 自愈：原 selector 未命中，改用备选 {healed}）",
                values=result.values,
                structured=result.structured,
            )

    async def _run_action(self, node: FlowNode, variables: RuntimeVariableStore, context: BrowserActionContext, *, timeout_ms: int) -> BrowserActionResult:
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

            # 先导航建立 origin 再清 localStorage/sessionStorage，避免残留 token 阻塞
            # SPA 初始化，随后 reload 使其以未登录态干净启动。
            if clear_storage:
                await _goto_with_retry(page, url, timeout=timeout)
                await page.evaluate("localStorage.clear(); sessionStorage.clear();")
                await page.reload(wait_until="domcontentloaded", timeout=timeout)
            else:
                await _goto_with_retry(page, url, timeout=timeout)

            return BrowserActionResult(action_type=action_type, detail=page.url, values=[page.url])

        if action_type == "browser.ensureLogin":
            url = variables.resolve_text(_read_required_string(node, "targetUrl"))
            await _goto_with_retry(page, url, timeout=timeout)
            # SPA 站点跳转/重定向需要短暂稳定期，否则登录态探测会读到中间态。
            await page.wait_for_timeout(1500)
            status = await _detect_login_state(page, node, variables)
            return BrowserActionResult(action_type=action_type, detail=f"{page.url} → {status}", values=[status])

        if action_type == "browser.tab.open":
            url = _read_optional_string(node, "targetUrl")
            new_page = await context.page.context.new_page()
            if url is not None:
                target_url = variables.resolve_text(url)
                await _goto_with_retry(new_page, target_url, timeout=timeout)
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
            try:
                count = await _count_locator(page, sel)
                base_message = str(exc.args[0]) if exc.args else str(exc)
                exc.args = (f"{base_message} [selector '{sel}' 页面匹配 {count} 个元素]", *exc.args[1:])
            except Exception:
                pass

        if action_type == "browser.click":
            force = _read_bool(node, "force", default=False)
            try:
                if force:
                    # force=True 绕过 Playwright 可操作性检查，可点击隐藏元素
                    # （visibility:hidden/opacity:0），用于登录前必须勾选的隐藏协议框。
                    target = _target_locator(page, selector).first
                    await target.wait_for(state="attached", timeout=timeout)
                    await target.click(force=True, timeout=timeout)
                else:
                    await _click_first_visible(page, selector, timeout=timeout)
            except Exception as exc:
                await _enrich_selector_error(exc, selector)
                raise
            return BrowserActionResult(action_type=action_type, detail=selector, values=[selector])

        if action_type == "browser.fill":
            input_value = variables.resolve_text(_read_required_string(node, "inputValue", fallback_keys=("value",)))
            fill_mode = _read_optional_string(node, "fillMode") or "type"
            if fill_mode == "js":
                target = _target_locator(page, selector).first
                try:
                    await target.wait_for(state="attached", timeout=timeout)
                except Exception as exc:
                    await _enrich_selector_error(exc, selector)
                    raise
                # locator.evaluate 直接作用在元素上，跨 iframe / Shadow DOM 均生效。
                await target.evaluate(
                    """(el, val) => {
                        const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                        if (nativeInputValueSetter) { nativeInputValueSetter.call(el, val); }
                        else { el.value = val; }
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    input_value,
                )
            elif fill_mode == "type":
                target = _target_locator(page, selector).first
                try:
                    await target.click(timeout=timeout)
                except Exception as exc:
                    await _enrich_selector_error(exc, selector)
                    raise
                try:
                    await page.keyboard.press("ControlOrMeta+A")
                    await page.keyboard.press("Backspace")
                    await page.keyboard.type(input_value)
                    # 事件补发到目标元素本身（activeElement 在 iframe 场景下指向 iframe 容器）。
                    await target.evaluate(
                        """(el) => {
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
                    await _target_locator(page, selector).first.fill(input_value, timeout=timeout)
                except Exception as exc:
                    await _enrich_selector_error(exc, selector)
                    raise
            return BrowserActionResult(action_type=action_type, detail=selector, values=[input_value])

        if action_type == "browser.press":
            key = variables.resolve_text(_read_required_string(node, "inputValue", fallback_keys=("key", "value")))
            if _is_page_level_press_selector(selector):
                # Element UI/Ant Design 等弹层常在选择后重建输入框，Escape 等全局按键直发页面更稳。
                await page.keyboard.press(key)
            else:
                try:
                    await _target_locator(page, selector).first.press(key, timeout=timeout)
                except Exception as exc:
                    await _enrich_selector_error(exc, selector)
                    raise
            return BrowserActionResult(action_type=action_type, detail=selector, values=[key])

        if action_type == "browser.wait":
            try:
                await _target_locator(page, selector).first.wait_for(state="visible", timeout=timeout)
            except Exception as exc:
                await _enrich_selector_error(exc, selector)
                raise
            return BrowserActionResult(action_type=action_type, detail=selector, values=[selector])

        if action_type == "browser.waitFor":
            # 与 browser.wait（只等可见）区分：支持等消失/等文本出现，覆盖"点击后
            # loading 遮罩消失""异步渲染出结果文案"这类 browser.wait 覆盖不到的同步场景。
            condition = _read_optional_string(node, "waitCondition") or "visible"
            try:
                target = _target_locator(page, selector).first
                if condition == "hidden":
                    await target.wait_for(state="hidden", timeout=timeout)
                elif condition == "textContains":
                    expected = variables.resolve_text(
                        _read_required_string(node, "inputValue", fallback_keys=("value",))
                    )
                    deadline = time.monotonic() + timeout / 1000
                    while True:
                        text = await target.text_content() or ""
                        if expected in text:
                            break
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f"等待文本超时（{timeout}ms）: 期望包含「{expected}」，当前「{text[:200]}」")
                        await page.wait_for_timeout(300)
                else:
                    await target.wait_for(state="visible", timeout=timeout)
            except Exception as exc:
                await _enrich_selector_error(exc, selector)
                raise
            return BrowserActionResult(action_type=action_type, detail=selector, values=[selector])

        if action_type == "browser.dismiss":
            dismissed = await self._dismiss_overlays(page, selector, variables, node, timeout=timeout)
            return BrowserActionResult(action_type=action_type, detail=selector, values=[str(dismissed)])

        if action_type == "browser.extract":
            rows = await _extract_locator_values(page, selector_config, timeout=timeout)
            rows, schema_note = _apply_output_schema(rows, node)
            return _build_extract_result(action_type, _with_schema_note(selector, schema_note), rows)

        if action_type == "browser.clickLoadMore":
            target_selector_config = _read_target_selector_config(node, variables)
            rows = await self._click_load_more_and_extract(page, selector, target_selector_config, variables, node, timeout=timeout)
            rows, schema_note = _apply_output_schema(rows, node)
            return _build_extract_result(action_type, _with_schema_note(f"{selector} -> {target_selector_config.selector}", schema_note), rows)

        if action_type == "browser.paginateNext":
            target_selector_config = _read_target_selector_config(node, variables)
            rows = await self._paginate_next_and_extract(page, selector, target_selector_config, variables, node, timeout=timeout)
            rows, schema_note = _apply_output_schema(rows, node)
            return _build_extract_result(action_type, _with_schema_note(f"{selector} -> {target_selector_config.selector}", schema_note), rows)

        if action_type == "browser.select":
            input_value = variables.resolve_text(_read_required_string(node, "inputValue", fallback_keys=("value",)))
            try:
                selected = await _target_locator(page, selector).first.select_option(input_value, timeout=timeout)
            except Exception as exc:
                await _enrich_selector_error(exc, selector)
                raise
            return BrowserActionResult(action_type=action_type, detail=selector, values=selected)

        if action_type == "browser.check":
            checked = _read_bool(node, "checked", default=True)
            target = _target_locator(page, selector).first
            try:
                if checked:
                    await target.check(timeout=timeout)
                else:
                    await target.uncheck(timeout=timeout)
            except Exception as exc:
                await _enrich_selector_error(exc, selector)
                raise
            return BrowserActionResult(action_type=action_type, detail=selector, values=[str(checked).lower()])

        if action_type == "browser.hover":
            try:
                await _target_locator(page, selector).first.hover(timeout=timeout)
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
                await _target_locator(page, selector).first.drag_to(_target_locator(page, target_selector).first, timeout=timeout)
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
            button = _first_locator(_target_locator(page, button_selector))
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

            next_button = _first_locator(_target_locator(page, next_selector))
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
            locator = _first_locator(_target_locator(page, candidate))
            if await _is_locator_hidden(locator) or await _is_locator_disabled(locator):
                continue
            await locator.click(timeout=timeout)
            dismissed += 1
            if delay_ms > 0:
                await page.wait_for_timeout(delay_ms)

        target_selector = _read_optional_string(node, "targetSelector")
        if target_selector is not None:
            resolved_target = variables.resolve_text(target_selector)
            await _first_locator(_target_locator(page, resolved_target)).wait_for(state="visible", timeout=timeout)

        count_variable = _read_optional_string(node, "dismissedCountVariable")
        if count_variable is not None:
            variables.set(count_variable, dismissed, scope="局部")
        return dismissed


def is_browser_action_node(node: FlowNode) -> bool:
    return node.get("type") in _BROWSER_ACTION_NODE_TYPES


def apply_browser_result_variables(node: FlowNode, result: BrowserActionResult, variables: RuntimeVariableStore) -> list[str]:
    # 节点执行成功后调用一次，按节点上配置的各输出字段名把同一份结果分别写入
    # outputVariable/firstValueVariable/countVariable/appendVariable，字段互不排斥可同时生效。
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


# 统一定位层：iframe 穿透
# selector 支持 "iframe选择器 >>> 内部选择器" 语法，按 ">>>" 分段依次 frame_locator
# 进入 iframe（可多层链式），末段定位目标元素；Shadow DOM 由 Playwright 原生穿透。

def _target_locator(page: object, selector: str) -> object:
    parts = [part.strip() for part in selector.split(">>>") if part.strip()]
    if len(parts) <= 1:
        return page.locator(selector.strip() or selector)
    scope: object = page
    for frame_selector in parts[:-1]:
        frame_locator = getattr(scope, "frame_locator", None)
        if not callable(frame_locator):
            # 测试替身等不支持 frame 的宿主：退回整串定位。
            return page.locator(selector)
        scope = frame_locator(frame_selector)
    return scope.locator(parts[-1])


# 运行时自愈 selector
# 主 selector 失败后按序探测备选：fallbackSelectors（换行分隔）→ anchorText
# 语义锚点派生的候选。探测只做 count()>0 判断，不产生页面副作用。

_HEALABLE_ACTIONS = {
    "browser.click", "browser.fill", "browser.press", "browser.wait", "browser.waitFor",
    "browser.extract", "browser.check", "browser.hover", "browser.select",
}


def _healing_candidates(node: FlowNode, variables: RuntimeVariableStore) -> list[str]:
    candidates: list[str] = []
    fallback_raw = _read_optional_string(node, "fallbackSelectors")
    if fallback_raw is not None:
        candidates.extend(_split_selector_candidates(variables.resolve_text(fallback_raw)))
    anchor = _read_optional_string(node, "anchorText")
    if anchor is not None:
        text = variables.resolve_text(anchor).strip().replace('"', '\\"')
        if text:
            candidates.extend([
                f'role=button[name="{text}"]',
                f'button:has-text("{text}")',
                f'a:has-text("{text}")',
                f'[role="button"]:has-text("{text}")',
                f'label:has-text("{text}")',
                f'text="{text}"',
            ])
    return candidates


async def _probe_selector(page: object, selector: str) -> bool:
    try:
        return await _count_locator(page, selector) > 0
    except Exception:
        return False


async def _iframe_frame_selectors(page: object, *, limit: int = 5) -> list[str]:
    try:
        metas = await page.eval_on_selector_all(
            "iframe",
            "els => els.slice(0, 8).map(e => ({id: e.id || '', name: e.getAttribute('name') || '', src: e.getAttribute('src') || ''}))",
        )
    except Exception:
        return []
    selectors: list[str] = []
    for meta in metas if isinstance(metas, list) else []:
        if not isinstance(meta, dict):
            continue
        frame_id = str(meta.get("id") or "").strip().replace('"', '\\"')
        name = str(meta.get("name") or "").strip().replace('"', '\\"')
        src = str(meta.get("src") or "").strip().replace('"', '\\"')
        if frame_id:
            selectors.append(f'iframe[id="{frame_id}"]')
        elif name:
            selectors.append(f'iframe[name="{name}"]')
        elif src:
            selectors.append(f'iframe[src="{src}"]')
        if len(selectors) >= limit:
            break
    return selectors


async def _heal_selector(page: object, node: FlowNode, variables: RuntimeVariableStore) -> str | None:
    action_type = _normalize_action_type(str(node.get("type") or ""))
    if action_type not in _HEALABLE_ACTIONS:
        return None
    primary = _read_optional_string(node, "selector")
    candidates = _healing_candidates(node, variables)
    for candidate in candidates:
        if candidate == primary:
            continue
        if await _probe_selector(page, candidate):
            return candidate
    # 主文档全部未命中 → 跨 iframe 探测：把主 selector 和各候选逐个放进每个
    # iframe 里再试（目标元素在 iframe 内是 selector "修不好" 的常见真因）。
    seeds = [sel for sel in [primary, *candidates] if sel and ">>>" not in sel]
    if seeds:
        for frame_css in await _iframe_frame_selectors(page):
            for seed in seeds:
                combined = f"{frame_css} >>> {seed}"
                if await _probe_selector(page, combined):
                    return combined
    return None


# Schema 驱动抓取（outputSchema）
# 节点声明期望的输出字段，运行时把提取行对齐成 schema 契约：
#   dict 行（table 模式）：表头 精确 → 别名 → 包含 匹配后改名为 schema 字段；
#   list 行（无表头表格）：按列序命名；纯文本行：单字段包装。
# 必需字段全部/部分未命中直接报错并列出实际可用列，驱动 AI 修复映射而不是
# 让错误数据静默流向下游。

def _parse_output_schema(node: FlowNode) -> list[dict[str, object]] | None:
    raw = node.get("outputSchema")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            raw = json.loads(text)
        except ValueError:
            # 容错：换行/逗号分隔的纯字段名列表。
            raw = [item.strip() for item in re.split(r"[\n,，]", text) if item.strip()]
    if not isinstance(raw, list):
        return None
    fields: list[dict[str, object]] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            fields.append({"name": item.strip(), "aliases": [], "required": True})
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            raw_aliases = item.get("aliases")
            aliases = [str(alias).strip() for alias in raw_aliases if str(alias).strip()] if isinstance(raw_aliases, list) else []
            fields.append({"name": name, "aliases": aliases, "required": bool(item.get("required", True))})
    return fields or None


def _match_schema_key(field: dict[str, object], keys: list[str]) -> str | None:
    probes = [str(field["name"]), *[str(alias) for alias in field["aliases"]]]
    lowered = {key.strip().lower(): key for key in keys}
    for probe in probes:
        hit = lowered.get(probe.strip().lower())
        if hit is not None:
            return hit
    for probe in probes:
        normalized = probe.strip().lower()
        if not normalized:
            continue
        for key in keys:
            key_normalized = key.strip().lower()
            if normalized in key_normalized or key_normalized in normalized:
                return key
    return None


def _apply_output_schema(rows: list[object], node: FlowNode) -> tuple[list[object], str | None]:
    schema = _parse_output_schema(node)
    if schema is None or not rows:
        return rows, None
    names = [str(field["name"]) for field in schema]

    dict_rows = [row for row in rows if isinstance(row, dict)]
    if dict_rows:
        keys: list[str] = []
        for row in dict_rows:
            for key in row.keys():
                if key not in keys:
                    keys.append(str(key))
        mapping = {str(field["name"]): _match_schema_key(field, keys) for field in schema}
        required_missing = [str(field["name"]) for field in schema if field["required"] and mapping[str(field["name"])] is None]
        if required_missing:
            raise ValueError(
                f"outputSchema 必需字段未命中: {required_missing}；实际可用列: {keys}。"
                "请为字段补充 aliases 别名、修正字段名，或调整提取 selector 范围"
            )
        aligned = [
            {name: str(row.get(mapping[name], "")).strip() if mapping[name] is not None else "" for name in names}
            for row in dict_rows
        ]
        optional_missing = [name for name in names if mapping[name] is None]
        matched = len(names) - len(optional_missing)
        note = f"schema 对齐 {matched}/{len(names)} 字段"
        if optional_missing:
            note += f"，可选字段缺失: {', '.join(optional_missing)}"
        return aligned, note

    list_rows = [row for row in rows if isinstance(row, list)]
    if list_rows:
        aligned = [
            {names[index]: str(row[index]).strip() if index < len(row) else "" for index in range(len(names))}
            for row in list_rows
        ]
        return aligned, f"schema 位置对齐 {len(names)} 字段（无表头，按列序命名）"

    if len(names) == 1:
        return [{names[0]: str(row).strip()} for row in rows], f"schema 单字段包装（{names[0]}）"

    raise ValueError(
        f"outputSchema 声明了 {len(names)} 个字段，但提取结果是纯文本行，无法对齐。"
        "请把 extractMode 改为 table，或将行 selector 收窄到结构化容器"
    )


# 阻断型浮层运行时检测
# 只识别，不破解/不自动关闭：命中后由 task_manager 转入人工接管等待。
# 不穷举第三方验证码厂商 class 名单，而是识别通用信号：①目标元素被遮挡
# （elementFromPoint 命中的不是目标本身）②无明确目标时兜底扫描大面积高层浮层；
# 已知验证码厂商特征仍保留作为高置信度快速命中。

# 判断元素是否为"挡路"容器 + 定位挡路元素，供 PROBE（只识别）和 DISMISS（尝试点击关闭）共用。
_OVERLAY_FINDER_JS = """
  const VENDOR_PATTERNS = [
    { re: /geetest_/i, label: '极验滑块验证' },
    { re: /dx_captcha/i, label: '顶象验证码' },
    { re: /shumei_captcha|SM_POP/i, label: '数美验证码' },
    { re: /tcaptcha/i, label: '腾讯防水墙验证' },
    { re: /nc_scale|nc-container/i, label: '阿里滑块验证' },
    { re: /captcha_verify|captcha_container|secsdk/i, label: '滑块/行为验证' },
    { re: /slider-verify|slide-verify|verify-wrap/i, label: '滑块验证' },
  ];

  const isVisible = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 2 || r.height <= 2) return false;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    if (parseFloat(style.opacity || '1') === 0) return false;
    return true;
  };

  const isOverlayContainer = (el) => {
    const style = window.getComputedStyle(el);
    if (style.position !== 'fixed' && style.position !== 'absolute') return false;
    const z = parseInt(style.zIndex || '0', 10) || 0;
    return z >= 10;
  };

  const findOverlayAncestor = (el) => {
    let node = el;
    let hops = 0;
    const viewportArea = window.innerWidth * window.innerHeight;
    while (node && node !== document.body && hops < 12) {
      if (isOverlayContainer(node)) {
        const r = node.getBoundingClientRect();
        if (r.width * r.height > viewportArea * 0.05) return node;
      }
      node = node.parentElement;
      hops += 1;
    }
    return el;
  };

  const findBlocker = (targetSelector) => {
    if (targetSelector) {
      let target = null;
      try { target = document.querySelector(targetSelector); } catch (e) { target = null; }
      if (target && isVisible(target)) {
        const r = target.getBoundingClientRect();
        const cx = r.left + r.width / 2;
        const cy = r.top + r.height / 2;
        const top = document.elementFromPoint(cx, cy);
        if (top && top !== target && !target.contains(top) && !top.contains(target)) {
          return { el: findOverlayAncestor(top), reason: 'target_obscured' };
        }
      }
    }

    const viewportArea = window.innerWidth * window.innerHeight;
    let best = null;
    let bestArea = 0;
    const candidates = document.querySelectorAll('body *');
    for (const el of candidates) {
      if (!isVisible(el) || !isOverlayContainer(el)) continue;
      const r = el.getBoundingClientRect();
      const area = r.width * r.height;
      if (area < viewportArea * 0.2) continue;
      if (area > bestArea) { bestArea = area; best = el; }
    }
    if (best) return { el: best, reason: 'fullscreen_overlay' };
    return null;
  };

  const classifyVendor = (el) => {
    const className = (el.className || '').toString().slice(0, 200);
    const haystack = className + ' ' + (el.outerHTML || '').slice(0, 500);
    for (const p of VENDOR_PATTERNS) {
      if (p.re.test(haystack)) return p.label;
    }
    return null;
  };
"""

_OVERLAY_PROBE_SCRIPT = (
    "(targetSelector) => {\n"
    + _OVERLAY_FINDER_JS
    + """
  const hit = findBlocker(targetSelector);
  if (!hit) return null;
  const blockerEl = hit.el;

  const className = (blockerEl.className || '').toString().slice(0, 200);
  const text = (blockerEl.innerText || '').trim().slice(0, 800);
  const vendor = classifyVendor(blockerEl);

  const interactive = [];
  blockerEl.querySelectorAll("button, a, input, [role='button']").forEach((el) => {
    if (interactive.length >= 12 || !isVisible(el)) return;
    interactive.push({
      tag: el.tagName.toLowerCase(),
      text: (el.innerText || el.value || el.placeholder || '').trim().slice(0, 60),
    });
  });

  return {
    reason: hit.reason,
    vendor,
    tag: blockerEl.tagName.toLowerCase(),
    id: blockerEl.id || null,
    className,
    text,
    interactive,
    hasIframe: !!blockerEl.querySelector('iframe'),
  };
}
"""
)

# 仅在 overlay 分类为安全类别（广告/隐私条款提示，见 _SAFE_AUTO_DISMISS_LABELS）时调用，
# 由 try_auto_dismiss_overlay 把关，避免误点验证码触发风控或误点未知弹层造成不可逆操作。
# 只在已识别的挡路容器内部找候选按钮，不搜索整页。
_OVERLAY_DISMISS_SCRIPT = (
    "(args) => {\n"
    + _OVERLAY_FINDER_JS
    + """
  const targetSelector = args && args.targetSelector;
  const allowConsent = !!(args && args.allowConsent);

  const hit = findBlocker(targetSelector);
  if (!hit) return { clicked: false, reason: 'no_blocker' };
  const blockerEl = hit.el;

  const CLOSE_KEYWORDS = ['关闭', '跳过', '不再提示', '我知道了', '取消', 'close', 'skip', 'dismiss', 'no thanks', 'got it'];
  const CONSENT_KEYWORDS = ['同意', '接受', 'accept', 'agree', 'confirm'];
  const CLOSE_ARIA_HINTS = ['close', 'dismiss', '关闭'];

  const candidates = Array.from(
    blockerEl.querySelectorAll("button, a, input[type='button'], input[type='submit'], [role='button']")
  ).filter(isVisible);

  const textOf = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().toLowerCase();
  const matchAny = (text, keywords) => keywords.some((kw) => text.includes(kw.toLowerCase()));

  let target = null;
  let category = null;

  for (const el of candidates) {
    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
    const cls = (el.className || '').toString().toLowerCase();
    if (matchAny(aria, CLOSE_ARIA_HINTS) || cls.includes('close') || cls.includes('dismiss')) {
      target = el; category = 'close'; break;
    }
  }

  if (!target) {
    for (const el of candidates) {
      const text = textOf(el);
      if (matchAny(text, CLOSE_KEYWORDS) || text === '×' || text === '✕' || text === 'x') {
        target = el; category = 'close'; break;
      }
    }
  }

  if (!target && allowConsent) {
    for (const el of candidates) {
      const text = textOf(el);
      if (matchAny(text, CONSENT_KEYWORDS)) {
        target = el; category = 'consent'; break;
      }
    }
  }

  if (!target) return { clicked: false, reason: 'no_candidate' };

  const buttonText = (target.innerText || target.value || target.getAttribute('aria-label') || '').trim().slice(0, 60);
  try {
    target.click();
  } catch (e) {
    return { clicked: false, reason: 'click_failed' };
  }
  return { clicked: true, category, buttonText };
}
"""
)

_KEYWORD_CATEGORY_RULES: list[tuple[tuple[str, ...], str]] = [
    (("验证", "滑块", "拖动", "拼图", "人机", "captcha", "verify", "slide"), "疑似验证码"),
    (("广告", "跳过", "领取", "限时", "活动", "优惠", "红包", "下载app"), "疑似广告弹窗"),
    (("cookie", "隐私", "同意", "使用条款", "consent"), "隐私/条款提示"),
]


def _classify_overlay(vendor: str | None, text: str, interactive: list[dict[str, object]]) -> str:
    if vendor:
        return vendor
    interactive_text = " ".join(str(item.get("text", "")) for item in interactive)
    haystack = f"{text} {interactive_text}".lower()
    for keywords, label in _KEYWORD_CATEGORY_RULES:
        if any(keyword.lower() in haystack for keyword in keywords):
            return label
    return "未知弹层"


@dataclass(frozen=True)
class OverlayInfo:
    """检测到的阻断型浮层：label 用于日志/横幅展示，summary 供 AI 弹层分析使用。"""

    label: str
    reason: str
    summary: dict[str, object]


async def detect_blocking_overlay(page: object, target_selector: str | None = None) -> OverlayInfo | None:
    """检测是否有阻断型浮层遮挡目标操作，或覆盖大部分视口。"""
    try:
        result = await page.evaluate(_OVERLAY_PROBE_SCRIPT, target_selector)
    except Exception:
        return None
    if not isinstance(result, dict):
        return None
    reason = str(result.get("reason") or "")
    label = _classify_overlay(
        result.get("vendor") if isinstance(result.get("vendor"), str) else None,
        str(result.get("text") or ""),
        result.get("interactive") if isinstance(result.get("interactive"), list) else [],
    )
    if reason == "fullscreen_overlay" and label == "未知弹层":
        # 兜底扫描命中大面积容器但无厂商特征/关键词匹配，很可能是页面自身布局容器
        # 而非真阻断浮层，置信度不足以转人工。
        return None
    return OverlayInfo(label=label, reason=reason, summary=result)


# 仅广告弹窗关闭/跳过、cookie同意/隐私条款确认这两类误点无不可逆后果；验证码
# （可能触发风控）和未知弹层（可能是"确认下单"等）永远不自动点击，只转人工。
_SAFE_AUTO_DISMISS_LABELS = {"疑似广告弹窗", "隐私/条款提示"}


@dataclass(frozen=True)
class DismissOutcome:
    category: str
    button_text: str


async def try_auto_dismiss_overlay(
    page: object,
    overlay: OverlayInfo,
    target_selector: str | None = None,
    *,
    allow_consent: bool = True,
) -> DismissOutcome | None:
    """尝试点击浮层内的"关闭/跳过"按钮，仅广告/隐私条款提示两类安全类别可用。
    返回 None 表示未处理，调用方应转人工接管；浮层是否真消失需另行调用 detect_blocking_overlay 复检。
    """
    if overlay.label not in _SAFE_AUTO_DISMISS_LABELS:
        return None
    allow_consent_click = bool(allow_consent) and overlay.label == "隐私/条款提示"
    try:
        result = await page.evaluate(
            _OVERLAY_DISMISS_SCRIPT, {"targetSelector": target_selector, "allowConsent": allow_consent_click}
        )
    except Exception:
        return None
    if not isinstance(result, dict) or not result.get("clicked"):
        return None
    return DismissOutcome(category=str(result.get("category") or ""), button_text=str(result.get("buttonText") or ""))


# 登录态探测（browser.ensureLogin）
# selector = 已登录特征（如用户头像）；targetSelector = 未登录特征（如密码框）。
# 都未配置时回退启发式：密码框可见或 URL 含 login → login_required。

async def _detect_login_state(page: object, node: FlowNode, variables: RuntimeVariableStore) -> str:
    logged_in_probe = _read_optional_string(node, "selector")
    logged_out_probe = _read_optional_string(node, "targetSelector")

    if logged_in_probe is not None:
        if await _probe_selector(page, variables.resolve_text(logged_in_probe)):
            return "logged_in"
    if logged_out_probe is not None:
        if await _probe_selector(page, variables.resolve_text(logged_out_probe)):
            return "login_required"
    if logged_in_probe is not None:
        # 显式配置了已登录特征但未命中 → 按需要登录处理。
        return "login_required"

    url = _read_page_url(page).lower()
    if "login" in url or "signin" in url or "passport" in url:
        return "login_required"
    if await _probe_selector(page, "input[type='password']"):
        return "login_required"
    return "logged_in"


async def _extract_locator_values(page: object, selector_config: SelectorConfig, *, timeout: int) -> list[object]:
    locator = _target_locator(page, selector_config.selector)
    await _first_locator(locator).wait_for(state="visible", timeout=timeout)
    if "," in selector_config.selector:
        await _raise_if_union_selector_collapses(locator, selector_config.selector)
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
        # 通用表格抓取：返回 list[dict]（自动识别表头）或 list[list]（无可用表头时）。
        # 保留真实对象而非预先 JSON 化，使下游 file.write/excel/JSON 输出走正常路径直接序列化。
        raw_rows = await locator.evaluate_all(_TABLE_EXTRACT_SCRIPT)
        _raise_if_table_scope_error(raw_rows, selector_config.selector)
        return _normalize_table_rows(raw_rows)
    else:
        raw_values = await locator.all_text_contents()
    return _clean_text_values(raw_values)


_TABLE_EXTRACT_SCRIPT = (
    "(elements) => {"
    " if (!elements.length) return [];"
    " const txt = (c) => (c && c.innerText ? c.innerText : '').replace(/\\s+/g, ' ').trim();"
    " const colNo = (el) => {"
    "   const raw = el.getAttribute && (el.getAttribute('aria-colindex') || el.getAttribute('data-colindex') || el.getAttribute('data-column-index'));"
    "   const n = raw ? Number(raw) : NaN;"
    "   return Number.isFinite(n) && n > 0 ? n : null;"
    " };"
    " const rowSelector = 'tr,[role=\"row\"]';"
    " const cellSelector = 'td,th,[role=\"cell\"],[role=\"gridcell\"],[role=\"columnheader\"]';"
    " const directCells = (row) => Array.from(row.children || []).filter((child) => child.matches && child.matches(cellSelector));"
    " const allCells = (row) => {"
    "   const direct = directCells(row);"
    "   if (direct.length) return direct;"
    "   const nested = Array.from(row.querySelectorAll(cellSelector));"
    "   return nested.filter((cell) => cell.closest(rowSelector) === row || cell.parentElement === row);"
    " };"
    " const table = elements[0].closest('table');"
    " const root = elements[0].closest('[role=grid],table') || (table ? table.parentElement : elements[0].parentElement);"
    " let headerPairs = [];"
    " if (root) {"
    "   const ths = root.querySelectorAll('thead th,[role=\"columnheader\"]');"
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
    "   const allC = allCells(row);"
    "   if (!allC.length) return [txt(row)].filter(Boolean);"
    "   const cells = allC.map((cell, i) => ({"
    "     index: i + 1,"
    "     col: colNo(cell),"
    "     text: txt(cell.querySelector('.cell') || cell)"
    "   }));"
    "   const hasColumnClasses = cells.some((c) => c.col) && headerByCol.size >= 2;"
    "   if (!hasColumnClasses) return cells.map((c) => c.text);"
    "   const obj = {};"
    "   cells.forEach((c) => {"
    "     const key = headerByCol.get(c.col) || headers[c.index - 1] || ('col_' + c.index);"
    "     obj[key] = c.text;"
    "   });"
    "   return obj;"
    " };"
    " const sourceRows = elements.flatMap((el) => {"
    "   if (el.matches && el.matches(rowSelector)) return [el];"
    "   return Array.from(el.querySelectorAll(rowSelector));"
    " });"
    " if (!sourceRows.length) return {__table_scope_error: 'no_rows_in_scope'};"
    # 只统计含 td/gridcell 的数据表：Element UI 会把表头拆成独立的纯 th 表格，那不算另一张表
    " const dataOwners = [];"
    " sourceRows.forEach((r) => {"
    "   const o = r.closest && r.closest('[role=\"grid\"],table');"
    "   if (o && dataOwners.indexOf(o) === -1 && o.querySelector('td,[role=\"gridcell\"]')) dataOwners.push(o);"
    " });"
    " if (dataOwners.length > 1) return {__table_scope_error: 'multiple_tables_in_scope', tableCount: dataOwners.length};"
    " const rows = sourceRows.map(cellsOf);"
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


def _with_schema_note(detail: str, schema_note: str | None) -> str:
    return detail if schema_note is None else f"{detail}（{schema_note}）"


def _build_extract_result(action_type: str, detail: str, rows: list[object]) -> "BrowserActionResult":
    """拆分为展示用字符串（SSE/摘要）和结构化行（输出变量）；纯文本/属性/html 抓取只走字符串。"""
    has_structured = any(isinstance(row, (dict, list)) for row in rows)
    if has_structured:
        values = [row if isinstance(row, str) else json.dumps(row, ensure_ascii=False) for row in rows]
        return BrowserActionResult(action_type=action_type, detail=detail, values=values, structured=list(rows))
    return BrowserActionResult(action_type=action_type, detail=detail, values=[str(row) for row in rows])


_UNION_CONTAINMENT_SCRIPT = (
    "(elements) => {"
    " const els = elements.slice(0, 120);"
    " for (let i = 0; i < els.length; i++) {"
    "   for (let j = 0; j < els.length; j++) {"
    "     if (i === j) continue;"
    "     if (els[i].contains(els[j])) {"
    "       const a = els[i];"
    "       return {"
    "         tag: (a.tagName || '').toLowerCase(),"
    "         cls: (a.getAttribute('class') || '').slice(0, 120),"
    "         matched: elements.length"
    "       };"
    "     }"
    "   }"
    " }"
    " return null;"
    "}"
)


async def _raise_if_union_selector_collapses(locator: object, selector: str) -> None:
    """并集 selector 里混进了祖先容器时立刻失败。

    'A, B, C' 里只要有一项是其它项的祖先，取并集就等于取那个祖先，更窄的几项形同虚设，
    抽到的是祖先的全部内容。写法上看不出来——每一项单独都是合理的业务 selector。
    """
    hit = await locator.evaluate_all(_UNION_CONTAINMENT_SCRIPT)
    if not isinstance(hit, dict):
        return
    ancestor = f"<{hit.get('tag')} class=\"{hit.get('cls')}\">"
    raise RuntimeError(
        f"selector {selector!r} 是多个选择器的并集，其中 {ancestor} 是其它命中元素的祖先容器。"
        f"并集会退化成只抓这个祖先的全部内容（本次共命中 {hit.get('matched')} 个元素），"
        "更窄的那几项完全不起作用，结果里会混进目标区域以外的整片页面。"
        "请只保留最贴近目标数据的那一项，不要把页面级容器和区块 selector 写在一起兜底。"
    )


def _raise_if_table_scope_error(raw_rows: object, selector: str) -> None:
    """table 模式的 selector 圈错范围时立刻失败，不返回看起来正常的行。"""
    if not isinstance(raw_rows, dict):
        return
    code = raw_rows.get("__table_scope_error")
    if code == "no_rows_in_scope":
        raise RuntimeError(
            f"extractMode=table 但 selector {selector!r} 命中的元素里没有任何表格行"
            "（tr / [role=row]）。请把 selector 收窄到真正的表格或行容器；"
            "若目标本来就不是表格（如指标卡片、列表项），应改用 text/attribute 模式。"
        )
    if code == "multiple_tables_in_scope":
        count = raw_rows.get("tableCount", "多")
        raise RuntimeError(
            f"extractMode=table 但 selector {selector!r} 同时命中了 {count} 张不同的表格，"
            "行会被跨表混在一起、表头错配。请把 selector 收窄到目标表格自身"
            "（如带业务类名的表格容器），不要指向页面级容器。"
        )


def _normalize_table_rows(raw_rows: object) -> list[object]:
    if not isinstance(raw_rows, list):
        return []
    rows: list[object] = []
    for row in raw_rows:
        if isinstance(row, dict):
            if any(str(v).strip() for v in row.values()):
                rows.append(row)
        elif isinstance(row, list):
            cells = [str(c).strip() for c in row]
            if any(cells):
                rows.append(cells)
        elif str(row).strip():
            rows.append(str(row).strip())
    return rows


async def _count_locator(page: object, selector: str) -> int:
    locator = _target_locator(page, selector)
    count = getattr(locator, "count", None)
    if not callable(count):
        return 0
    value = await count()
    return value if isinstance(value, int) else 0


async def _click_first_visible(page: object, selector: str, *, timeout: int) -> None:
    locator = _target_locator(page, selector)
    count = await _count_locator(page, selector)
    if count <= 1:
        await _first_locator(locator).click(timeout=timeout)
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
    await _first_locator(locator).click(timeout=timeout)


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
