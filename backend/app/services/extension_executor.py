"""基于浏览器扩展的 BrowserExecutor 实现，操作用户已打开的真实 Chrome 窗口
（区别于 BrowserActionRunner 的 Playwright 实现）。

依赖用户浏览器窗口保持打开，不支持无人值守定时执行——调用方需在路由前检查
bridge.is_connected。
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass

from app.services.browser_action_runner import (
    _HEALABLE_ACTIONS,
    BrowserActionResult,
    FlowNode,
    SelectorConfig,
    SweepOutcome,
    _build_extract_result,
    _healing_candidates,
    _normalize_action_type,
    _normalize_table_rows,
    _read_action_type,
    _read_bool,
    _read_int,
    _read_optional_string,
    _read_required_string,
    _read_selector_config,
    _read_target_selector_config,
    _split_selector_candidates,
)
from app.services.extension_bridge_service import ExtensionBridgeService
from app.services.pagination_probe import (
    EXTENSION_EVIDENCE_SELECTORS,
    FIRST_PAGE_STOP_REASONS,
    SINGLE_PAGE_VERDICT,
    PaginationEvidence,
    build_first_page_stop_error,
)
from app.services.runtime_variables import RuntimeVariableStore

_SUPPORTED_ACTION_TYPES = {
    "browser.screenshot",
    "browser.click",
    "browser.fill",
    "browser.extract",
    "browser.wait",
    "browser.waitFor",
    "browser.hover",
    "browser.select",
    "browser.press",
    "browser.scroll",
    "browser.open",
    "browser.tab.open",
    "browser.tab.close",
    "browser.tab.switch",
    "browser.check",
    "browser.drag",
    "browser.dismiss",
    "browser.clickLoadMore",
    "browser.paginateNext",
    "browser.ensureLogin",
}
_WAIT_POLL_INTERVAL_SECONDS = 0.3
_SCREENSHOT_MIN_INTERVAL_SECONDS = 1.0


@dataclass
class ExtensionExecutionContext:
    """占位上下文：插件执行器没有需要持有的浏览器进程句柄，用户的 Chrome 窗口本身就是上下文。"""

    headless: bool = False
    persistent: bool = True


_DEFAULT_MAX_STEPS_PER_RUN = 500


class ExtensionExecutor:
    """`max_steps_per_run` 限制单次运行动作数——扩展驱动的是用户已登录的真实浏览器会话，
    失控循环的风险比 Playwright 一次性 profile 大得多。"""

    def __init__(self, bridge: ExtensionBridgeService, *, max_steps_per_run: int = _DEFAULT_MAX_STEPS_PER_RUN) -> None:
        self._bridge = bridge
        self._max_steps_per_run = max_steps_per_run
        self._step_count = 0
        self._last_screenshot_at: float | None = None

    @property
    def is_connected(self) -> bool:
        return self._bridge.is_connected

    # owner 只为满足 BrowserExecutor 协议：插件执行器借用用户自己的浏览器，不占用应用的 profile
    async def create_context(self, *, headless: bool = True, owner: str | None = None) -> ExtensionExecutionContext:
        if not self._bridge.is_connected:
            raise ConnectionError("没有已连接的浏览器扩展，无法使用插件执行器——请确认扩展已加载并打开了一个标签页")
        self._step_count = 0
        await self._ensure_tab_group()
        return ExtensionExecutionContext()

    async def close_context(self, context: ExtensionExecutionContext | None) -> None:
        await self._mark_tab_group_done()
        return

    async def _ensure_tab_group(self) -> None:
        """标签页分组仅作可视化隔离，失败不应阻断真实流程执行。"""
        try:
            await self._bridge.execute({"type": "automation.group.start", "title": "Easy RPA 执行中"}, timeout=3.0)
        except Exception:
            pass

    async def _mark_tab_group_done(self) -> None:
        try:
            await self._bridge.execute({"type": "automation.group.end", "title": "Easy RPA 已完成"}, timeout=3.0)
        except Exception:
            pass

    async def screenshot(self, context: ExtensionExecutionContext) -> bytes:
        # captureVisibleTab 只截可见视口且有 Chrome 侧调用配额，这里节流避免连续截图触发限流。
        if self._last_screenshot_at is not None:
            elapsed = time.monotonic() - self._last_screenshot_at
            remaining = _SCREENSHOT_MIN_INTERVAL_SECONDS - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._last_screenshot_at = time.monotonic()
        result = await self._bridge.execute({"type": "browser.screenshot"}, timeout=10.0)
        data_url = str(result.get("dataUrl", ""))
        _, _, encoded = data_url.partition(",")
        return base64.b64decode(encoded) if encoded else b""

    async def show_takeover_banner(self, task_id: str, message: str) -> None:
        """在真实浏览器标签页（而非 Easy RPA 应用窗口）顶部插入提示条，因为插件执行器运行时用户看的是前者。"""
        await self._bridge.execute({"type": "takeover.show", "message": message, "taskId": task_id}, timeout=5.0)

    async def hide_takeover_banner(self) -> None:
        await self._bridge.execute({"type": "takeover.hide"}, timeout=5.0)

    async def _highlight_best_effort(self, selector: str) -> None:
        """纯视觉反馈，失败或超时不应影响实际动作。"""
        try:
            await self._bridge.execute({"type": "highlight", "selector": selector, "durationMs": 900}, timeout=2.0)
        except Exception:
            pass

    async def run(
        self, node: FlowNode, variables: RuntimeVariableStore, context: ExtensionExecutionContext, *, timeout_ms: int
    ) -> BrowserActionResult:
        self._step_count += 1
        if self._step_count > self._max_steps_per_run:
            raise RuntimeError(
                f"插件执行器单次运行动作数已达上限（{self._max_steps_per_run}），已停止执行——"
                "这是为了防止失控循环对用户真实登录态的浏览器做出无限次操作。"
            )
        try:
            return await self._run_action(node, variables, context, timeout_ms=timeout_ms)
        except Exception:
            healed = await self._heal_selector(node, variables)
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

    async def _heal_selector(self, node: FlowNode, variables: RuntimeVariableStore) -> str | None:
        """复用 BrowserActionRunner 的候选生成逻辑，探测改走 browser.elementState；
        content.ts 的 querySelectorDeep 已自动穿透 shadow root/同源 iframe，无需拼 iframe 前缀。"""
        action_type = _normalize_action_type(str(node.get("type") or ""))
        if action_type not in _HEALABLE_ACTIONS:
            return None
        primary = _read_optional_string(node, "selector")
        for candidate in _healing_candidates(node, variables):
            if candidate == primary:
                continue
            try:
                state = await self._element_state(candidate, timeout_seconds=5.0)
            except Exception:
                continue
            if bool(state.get("exists", False)) and not bool(state.get("hidden", True)):
                return candidate
        return None

    async def _run_action(
        self, node: FlowNode, variables: RuntimeVariableStore, context: ExtensionExecutionContext, *, timeout_ms: int
    ) -> BrowserActionResult:
        action_type = _normalize_action_type(_read_action_type(node))
        if action_type not in _SUPPORTED_ACTION_TYPES:
            raise ValueError(
                f"插件执行器暂不支持节点类型: {action_type}"
                "（当前支持 screenshot/click/fill/extract/wait/waitFor/hover/select/press/scroll/open/tab.open/tab.close/"
                "tab.switch/check/drag/dismiss/clickLoadMore/paginateNext/ensureLogin）"
            )

        timeout_seconds = max(1.0, timeout_ms / 1000)

        # trustedInput: true 时改走 background 的 chrome.debugger(CDP) 可信输入路径，
        # 应对检查 event.isTrusted 而拒绝 dispatchEvent 合成事件的站点。
        trusted_input = _read_bool(node, "trustedInput", default=False)

        if action_type == "browser.screenshot":
            # 纯诊断标记节点，真正截图由 task_manager._save_browser_screenshot 生成，此处不重复触发。
            return BrowserActionResult(action_type=action_type, detail="", values=[])

        if action_type == "browser.open":
            # clearStorage/clearCookies 对插件驱动的用户真实登录态浏览器无意义，交给 background 拒绝。
            target_url = variables.resolve_text(_read_required_string(node, "targetUrl"))
            open_payload: dict[str, object] = {"type": "browser.open", "targetUrl": target_url}
            if _read_bool(node, "clearStorage", default=False):
                open_payload["clearStorage"] = True
            if _read_bool(node, "clearCookies", default=False):
                open_payload["clearCookies"] = True
            result = await self._bridge.execute(open_payload, timeout=timeout_seconds)
            url = str(result.get("url", target_url))
            return BrowserActionResult(action_type=action_type, detail=url, values=[url])

        if action_type == "browser.tab.open":
            raw_url = _read_optional_string(node, "targetUrl")
            payload: dict[str, object] = {"type": "browser.tab.open"}
            if raw_url is not None:
                payload["targetUrl"] = variables.resolve_text(raw_url)
            result = await self._bridge.execute(payload, timeout=timeout_seconds)
            url = str(result.get("url", ""))
            return BrowserActionResult(action_type=action_type, detail=url, values=[url])

        if action_type == "browser.tab.switch":
            index = _read_int(node, "index", default=0)
            result = await self._bridge.execute({"type": "browser.tab.switch", "index": index}, timeout=timeout_seconds)
            url = str(result.get("url", ""))
            return BrowserActionResult(action_type=action_type, detail=url, values=[url])

        if action_type == "browser.tab.close":
            result = await self._bridge.execute({"type": "browser.tab.close"}, timeout=timeout_seconds)
            url = str(result.get("url", ""))
            return BrowserActionResult(action_type=action_type, detail=url, values=[url])

        if action_type == "browser.click":
            selector = variables.resolve_text(_read_required_string(node, "selector"))
            await self._highlight_best_effort(selector)
            await self._bridge.execute(
                {"type": "browser.click", "selector": selector, "trusted": trusted_input}, timeout=timeout_seconds
            )
            return BrowserActionResult(action_type=action_type, detail=selector, values=[selector])

        if action_type == "browser.fill":
            selector = variables.resolve_text(_read_required_string(node, "selector"))
            input_value = variables.resolve_text(_read_required_string(node, "inputValue", fallback_keys=("value",)))
            await self._highlight_best_effort(selector)
            await self._bridge.execute(
                {"type": "browser.fill", "selector": selector, "inputValue": input_value, "trusted": trusted_input},
                timeout=timeout_seconds,
            )
            return BrowserActionResult(action_type=action_type, detail=selector, values=[input_value])

        if action_type == "browser.extract":
            selector_config = _read_selector_config(node, variables)
            if selector_config.extract_mode not in {"text", "count", "attribute", "html", "table"}:
                raise ValueError(
                    f"插件执行器暂不支持 extractMode={selector_config.extract_mode}，"
                    "目前支持 text/count/attribute/html/table"
                )
            payload: dict[str, object] = {
                "type": "browser.extract",
                "selector": selector_config.selector,
                "extractMode": selector_config.extract_mode,
            }
            if selector_config.attribute is not None:
                payload["attribute"] = selector_config.attribute
            result = await self._bridge.execute(payload, timeout=timeout_seconds)
            raw_values = result.get("values")
            if selector_config.extract_mode == "table":
                rows = _normalize_table_rows(raw_values)
                return _build_extract_result(action_type, selector_config.selector, rows)
            if isinstance(raw_values, list):
                values = [str(v) for v in raw_values]
            else:
                values = [str(result.get("text", ""))]
            return BrowserActionResult(action_type=action_type, detail=selector_config.selector, values=values)

        if action_type == "browser.hover":
            selector = variables.resolve_text(_read_required_string(node, "selector"))
            await self._highlight_best_effort(selector)
            await self._bridge.execute({"type": "browser.hover", "selector": selector}, timeout=timeout_seconds)
            return BrowserActionResult(action_type=action_type, detail=selector, values=[selector])

        if action_type == "browser.select":
            selector = variables.resolve_text(_read_required_string(node, "selector"))
            input_value = variables.resolve_text(_read_required_string(node, "inputValue", fallback_keys=("value",)))
            result = await self._bridge.execute(
                {"type": "browser.select", "selector": selector, "inputValue": input_value}, timeout=timeout_seconds
            )
            selected = result.get("selected")
            values = [str(v) for v in selected] if isinstance(selected, list) else [input_value]
            return BrowserActionResult(action_type=action_type, detail=selector, values=values)

        if action_type == "browser.press":
            key = variables.resolve_text(_read_required_string(node, "inputValue", fallback_keys=("key", "value")))
            raw_selector = _read_optional_string(node, "selector")
            selector = variables.resolve_text(raw_selector) if raw_selector is not None else None
            action_payload: dict[str, object] = {"type": "browser.press", "inputValue": key}
            if selector is not None:
                action_payload["selector"] = selector
            await self._bridge.execute(action_payload, timeout=timeout_seconds)
            return BrowserActionResult(action_type=action_type, detail=selector or "(page)", values=[key])

        if action_type == "browser.scroll":
            distance = _read_int(node, "distance", default=800)
            await self._bridge.execute({"type": "browser.scroll", "distance": distance}, timeout=timeout_seconds)
            return BrowserActionResult(action_type=action_type, detail=str(distance), values=[str(distance)])

        if action_type == "browser.check":
            selector = variables.resolve_text(_read_required_string(node, "selector"))
            checked = _read_bool(node, "checked", default=True)
            result = await self._bridge.execute(
                {"type": "browser.check", "selector": selector, "checked": checked}, timeout=timeout_seconds
            )
            actual = bool(result.get("checked", checked))
            return BrowserActionResult(action_type=action_type, detail=selector, values=[str(actual).lower()])

        if action_type == "browser.drag":
            selector = variables.resolve_text(_read_required_string(node, "selector"))
            target_selector = variables.resolve_text(_read_required_string(node, "targetSelector", fallback_keys=("target",)))
            await self._bridge.execute(
                {"type": "browser.drag", "selector": selector, "targetSelector": target_selector}, timeout=timeout_seconds
            )
            return BrowserActionResult(action_type=action_type, detail=f"{selector} -> {target_selector}", values=[target_selector])

        if action_type == "browser.ensureLogin":
            logged_in_probe = _read_optional_string(node, "selector")
            logged_out_probe = _read_optional_string(node, "targetSelector")
            payload: dict[str, object] = {"type": "browser.ensureLogin"}
            if logged_in_probe is not None:
                payload["selector"] = variables.resolve_text(logged_in_probe)
            if logged_out_probe is not None:
                payload["targetSelector"] = variables.resolve_text(logged_out_probe)
            result = await self._bridge.execute(payload, timeout=timeout_seconds)
            status = str(result.get("status", "logged_in"))
            return BrowserActionResult(action_type=action_type, detail=status, values=[status])

        if action_type == "browser.waitFor":
            # 区别于只轮询"是否存在"的 browser.wait，还支持等消失/等文本包含。
            selector = variables.resolve_text(_read_required_string(node, "selector"))
            condition = _read_optional_string(node, "waitCondition") or "visible"
            if condition == "textContains":
                expected = variables.resolve_text(_read_required_string(node, "inputValue", fallback_keys=("value",)))
                await self._wait_for_text_contains(selector, expected, timeout_ms=timeout_ms)
            else:
                hidden = condition == "hidden"
                await self._wait_for_visibility(selector, hidden=hidden, timeout_ms=timeout_ms)
            return BrowserActionResult(action_type=action_type, detail=selector, values=[selector])

        if action_type == "browser.dismiss":
            raw_selector = variables.resolve_text(_read_required_string(node, "selector"))
            dismissed = await self._dismiss_overlays(raw_selector, variables, node, timeout_seconds=timeout_seconds)
            return BrowserActionResult(action_type=action_type, detail=raw_selector, values=[str(dismissed)])

        if action_type == "browser.clickLoadMore":
            button_selector = variables.resolve_text(_read_required_string(node, "selector"))
            target_selector_config = _read_target_selector_config(node, variables)
            outcome = await self._click_load_more_and_extract(button_selector, target_selector_config, variables, node, timeout_seconds=timeout_seconds)
            detail = f"{button_selector} -> {target_selector_config.selector} · {outcome.note}"
            return _build_extract_result(action_type, detail, outcome.rows)

        if action_type == "browser.paginateNext":
            url_template = _read_optional_string(node, "urlTemplate")
            if url_template is not None:
                resolved_template = variables.resolve_text(url_template)
                if "${page}" not in resolved_template:
                    raise ValueError("urlTemplate 必须包含 ${page} 占位符，否则每一页请求的都是同一个地址")
                target_selector_config = _read_target_selector_config(node, variables)
                outcome = await self._paginate_by_url_and_extract(
                    resolved_template, target_selector_config, variables, node, timeout_seconds=timeout_seconds
                )
                detail = f"{resolved_template} -> {target_selector_config.selector} · {outcome.note}"
                return _build_extract_result(action_type, detail, outcome.rows)
            next_selector = variables.resolve_text(_read_required_string(node, "selector"))
            target_selector_config = _read_target_selector_config(node, variables)
            outcome = await self._paginate_next_and_extract(next_selector, target_selector_config, variables, node, timeout_seconds=timeout_seconds)
            detail = f"{next_selector} -> {target_selector_config.selector} · {outcome.note}"
            return _build_extract_result(action_type, detail, outcome.rows)

        selector = variables.resolve_text(_read_required_string(node, "selector"))
        await self._wait_for_selector(selector, timeout_ms=timeout_ms)
        return BrowserActionResult(action_type=action_type, detail=selector, values=[selector])

    async def _wait_for_selector(self, selector: str, *, timeout_ms: int) -> None:
        deadline = time.monotonic() + timeout_ms / 1000
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                await self._bridge.execute({"type": "browser.extract", "selector": selector}, timeout=5.0)
                return
            except RuntimeError as exc:
                last_error = exc
                await asyncio.sleep(_WAIT_POLL_INTERVAL_SECONDS)
        raise TimeoutError(f"等待元素超时（{timeout_ms}ms）: {selector}") from last_error

    async def _wait_for_visibility(self, selector: str, *, hidden: bool, timeout_ms: int) -> None:
        """hidden=True 要求元素消失或不可见，hidden=False 要求存在且可见。"""
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            state = await self._element_state(selector, timeout_seconds=5.0)
            exists = bool(state.get("exists", False))
            is_hidden = bool(state.get("hidden", True))
            satisfied = (not exists or is_hidden) if hidden else (exists and not is_hidden)
            if satisfied:
                return
            if time.monotonic() >= deadline:
                target_desc = "消失" if hidden else "出现并可见"
                raise TimeoutError(f"等待元素{target_desc}超时（{timeout_ms}ms）: {selector}")
            await asyncio.sleep(_WAIT_POLL_INTERVAL_SECONDS)

    async def _wait_for_text_contains(self, selector: str, expected: str, *, timeout_ms: int) -> None:
        deadline = time.monotonic() + timeout_ms / 1000
        last_text = ""
        while True:
            try:
                result = await self._bridge.execute({"type": "browser.extract", "selector": selector}, timeout=5.0)
                last_text = str(result.get("text", ""))
                if expected in last_text:
                    return
            except RuntimeError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"等待文本超时（{timeout_ms}ms）: 期望包含「{expected}」，当前「{last_text[:200]}」")
            await asyncio.sleep(_WAIT_POLL_INTERVAL_SECONDS)

    @staticmethod
    def _require_text_extract_mode(selector_config: SelectorConfig) -> None:
        if selector_config.extract_mode != "text":
            raise ValueError(f"插件执行器该复合动作暂不支持 extractMode={selector_config.extract_mode}，目前仅支持 text")

    async def _element_state(self, selector: str, *, timeout_seconds: float) -> dict[str, object]:
        return await self._bridge.execute({"type": "browser.elementState", "selector": selector}, timeout=timeout_seconds)

    async def _extract_selector_values(self, selector_config: SelectorConfig, *, timeout_seconds: float) -> list[object]:
        payload: dict[str, object] = {
            "type": "browser.extract",
            "selector": selector_config.selector,
            "extractMode": selector_config.extract_mode,
        }
        if selector_config.attribute is not None:
            payload["attribute"] = selector_config.attribute
        result = await self._bridge.execute(payload, timeout=timeout_seconds)
        values = result.get("values")
        if selector_config.extract_mode == "table":
            return _normalize_table_rows(values)
        if isinstance(values, list):
            return [str(v) for v in values]
        text = str(result.get("text", ""))
        return [text] if text else []

    async def _dismiss_overlays(
        self, selector: str, variables: RuntimeVariableStore, node: FlowNode, *, timeout_seconds: float
    ) -> int:
        selectors = _split_selector_candidates(selector)
        delay_ms = max(0, _read_int(node, "delayMs", default=200))
        max_iterations = max(1, _read_int(node, "maxIterations", default=len(selectors) or 1))
        dismissed = 0

        for candidate in selectors[:max_iterations]:
            state = await self._element_state(candidate, timeout_seconds=timeout_seconds)
            if state.get("hidden", True) or state.get("disabled", False):
                continue
            await self._bridge.execute({"type": "browser.click", "selector": candidate}, timeout=timeout_seconds)
            dismissed += 1
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000)

        target_selector = _read_optional_string(node, "targetSelector")
        if target_selector is not None:
            resolved_target = variables.resolve_text(target_selector)
            await self._wait_for_selector(resolved_target, timeout_ms=int(timeout_seconds * 1000))

        count_variable = _read_optional_string(node, "dismissedCountVariable")
        if count_variable is not None:
            variables.set(count_variable, dismissed, scope="局部")
        return dismissed

    async def _click_load_more_and_extract(
        self,
        button_selector: str,
        target_selector_config: SelectorConfig,
        variables: RuntimeVariableStore,
        node: FlowNode,
        *,
        timeout_seconds: float,
    ) -> SweepOutcome:
        max_iterations = max(1, _read_int(node, "maxIterations", default=5))
        delay_ms = max(0, _read_int(node, "delayMs", default=500))
        current_values = await self._extract_selector_values(target_selector_config, timeout_seconds=timeout_seconds)
        previous_count = len(current_values)
        counts = [previous_count]
        clicks = 0
        exists = False
        stop_reason = "max_iterations_reached"

        for _index in range(max_iterations):
            state = await self._element_state(button_selector, timeout_seconds=timeout_seconds)
            exists = bool(state.get("exists", False))
            if not exists:
                stop_reason = "trigger_not_found"
                break
            if state.get("hidden", True):
                stop_reason = "trigger_hidden"
                break
            await self._bridge.execute({"type": "browser.click", "selector": button_selector}, timeout=timeout_seconds)
            clicks += 1
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000)
            next_values = await self._extract_selector_values(target_selector_config, timeout_seconds=timeout_seconds)
            if len(next_values) <= previous_count:
                stop_reason = "no_new_items"
                break
            previous_count = len(next_values)
            counts.append(previous_count)
            current_values = next_values

        count_variable = _read_optional_string(node, "loadedCountVariable")
        if count_variable is not None:
            variables.set(count_variable, previous_count, scope="局部")
        return SweepOutcome(
            rows=current_values,
            rounds=clicks + 1,
            progress_counts=counts,
            stop_reason=stop_reason,
            trigger_matches=1 if exists else 0,
            unit=" 轮加载",
        )

    async def _paginate_next_and_extract(
        self,
        next_selector: str,
        target_selector_config: SelectorConfig,
        variables: RuntimeVariableStore,
        node: FlowNode,
        *,
        timeout_seconds: float,
    ) -> SweepOutcome:
        max_iterations = max(1, _read_int(node, "maxIterations", default=20))
        delay_ms = max(0, _read_int(node, "delayMs", default=500))
        pages_visited = 0
        all_values: list[object] = []
        per_page_counts: list[int] = []
        previous_fingerprint = ""
        stop_reason = "max_iterations_reached"
        exists = False

        for _index in range(max_iterations):
            current_values = await self._extract_selector_values(target_selector_config, timeout_seconds=timeout_seconds)
            all_values.extend(current_values)
            per_page_counts.append(len(current_values))
            pages_visited += 1

            state = await self._element_state(next_selector, timeout_seconds=timeout_seconds)
            exists = bool(state.get("exists", False))
            if not exists:
                # 末页的按钮仍然 exists，只是 hidden/disabled；不存在只能是 selector 错了
                stop_reason = "next_selector_not_found"
                break
            if state.get("hidden", True):
                stop_reason = "next_button_hidden"
                break
            if state.get("disabled", False):
                stop_reason = "next_button_disabled"
                break

            before_fingerprint = _fingerprint_rows(current_values)
            await self._bridge.execute({"type": "browser.click", "selector": next_selector}, timeout=timeout_seconds)
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000)
            after_values = await self._extract_selector_values(target_selector_config, timeout_seconds=timeout_seconds)
            after_fingerprint = _fingerprint_rows(after_values)
            if after_fingerprint == before_fingerprint or after_fingerprint == previous_fingerprint:
                stop_reason = "duplicate_content"
                break
            previous_fingerprint = before_fingerprint

        pages_variable = _read_optional_string(node, "pageCountVariable")
        if pages_variable is not None:
            variables.set(pages_variable, pages_visited, scope="局部")

        verdict: str | None = None
        if pages_visited <= 1 and stop_reason in FIRST_PAGE_STOP_REASONS:
            # 判据与 Playwright 执行器一致，见 browser_action_runner._paginate_next_and_extract
            evidence = await self._probe_pagination_evidence(timeout_seconds=timeout_seconds)
            if stop_reason == "next_selector_not_found" or evidence.has_more_pages:
                raise ValueError(build_first_page_stop_error(
                    next_selector=next_selector,
                    stop_reason=stop_reason,
                    row_count=len(all_values),
                    evidence=evidence,
                ))
            verdict = SINGLE_PAGE_VERDICT

        return SweepOutcome(
            rows=all_values,
            rounds=pages_visited,
            progress_counts=per_page_counts,
            stop_reason=stop_reason,
            trigger_matches=1 if exists else 0,
            unit=" 页",
            verdict=verdict,
        )

    async def _probe_pagination_evidence(self, *, timeout_seconds: float) -> PaginationEvidence:
        """插件没有 evaluate 通道，只能拿固定 CSS 逐个问 elementState 换取证据。

        拿不到 href，因此只能证明「还有分页控件」，给不出 urlTemplate 建议；
        这条路径只在翻页已经停在第 1 页时才走，多这几次往返不影响正常运行。
        """
        candidates: list[dict[str, object]] = []
        for selector in EXTENSION_EVIDENCE_SELECTORS:
            try:
                state = await self._element_state(selector, timeout_seconds=min(timeout_seconds, 3.0))
            except Exception:
                continue
            if not state.get("exists", False) or state.get("hidden", True):
                continue
            kind = "page_number" if "=2" in selector else "next_control"
            candidates.append({"kind": kind, "text": "", "href": selector, "selector": selector})
        return PaginationEvidence(candidates=candidates)

    async def _paginate_by_url_and_extract(
        self,
        url_template: str,
        target_selector_config: SelectorConfig,
        variables: RuntimeVariableStore,
        node: FlowNode,
        *,
        timeout_seconds: float,
    ) -> SweepOutcome:
        """按 URL 逐页抓取，判据与 browser_action_runner._paginate_by_url_and_extract 一致。"""
        max_iterations = max(1, _read_int(node, "maxIterations", default=20))
        start_page = _read_int(node, "startPage", default=1)
        page_step = max(1, _read_int(node, "pageStep", default=1))
        delay_ms = max(0, _read_int(node, "delayMs", default=500))

        all_values: list[object] = []
        per_page_counts: list[int] = []
        previous_fingerprint = ""
        pages_visited = 0
        stop_reason = "max_iterations_reached"

        for index in range(max_iterations):
            page_number = start_page + index * page_step
            target_url = url_template.replace("${page}", str(page_number))
            await self._bridge.execute({"type": "browser.open", "targetUrl": target_url}, timeout=timeout_seconds)
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000)
            try:
                current_values = await self._extract_selector_values(target_selector_config, timeout_seconds=timeout_seconds)
            except RuntimeError:
                # 翻过头是这条路径的正常收尾方式：末页之后行选择器在插件端报「没找到元素」，
                # 这属于「没有下一页了」，不是流程失败。第 1 页就失败会走下面的 pages_visited==0 分支
                current_values = []
            if not current_values:
                stop_reason = "empty_page"
                break
            fingerprint = _fingerprint_rows(current_values)
            if fingerprint == previous_fingerprint:
                stop_reason = "duplicate_content"
                break
            all_values.extend(current_values)
            per_page_counts.append(len(current_values))
            pages_visited += 1
            previous_fingerprint = fingerprint

        pages_variable = _read_optional_string(node, "pageCountVariable")
        if pages_variable is not None:
            variables.set(pages_variable, pages_visited, scope="局部")

        if pages_visited == 0:
            raise ValueError(
                f"按 URL 翻页第 1 页（{url_template.replace('${page}', str(start_page))}）就没抽到任何内容："
                f"targetSelector `{target_selector_config.selector}` 没命中，或 startPage 起点不对。"
                "请用 inspect_page 核对行选择器与真实页号起点（有的站从 0 开始，offset 型分页要配 pageStep）。"
            )

        return SweepOutcome(
            rows=all_values,
            rounds=pages_visited,
            progress_counts=per_page_counts,
            stop_reason=stop_reason,
            trigger_matches=None,
            unit=" 页",
        )


def _fingerprint_rows(rows: list[object]) -> str:
    return json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
