// 内容脚本：读 DOM、用合成事件模拟操作。合成事件 isTrusted 恒 false，校验该字段的站点需 background 走 CDP（见 resolveRect）。
// 定位走「快照 ref → Element」映射而非重算 selector，导航/重载后失效。

import type { ContentAction } from './content/types';
import {
  captureSnapshot,
  findElements,
  isVisible,
  observePage,
  probeSelectorVisible,
  querySelectorAllDeep,
  registerActionTarget,
  resolveElement,
  tryResolveElement,
} from './content/dom';
// 取证脚本与 Playwright 通道同一个文件，不是各自一份：见该文件头部注释。
import { EFFECT_SIGNATURE, TARGET_STATE } from '../../backend/app/services/ai_tools/page_effect.js';
import { dispatchExtract, dispatchExtractAll } from './content/extract';
import { markAutomationActivity, moveCursorTo, pulseClickAt, highlightElement, setPageBlocked } from './content/automationVisual';
import { hideTakeoverBanner, showTakeoverBanner } from './content/takeoverBanner';
import { dismissBlockingOverlays } from './content/modalGuard';
// Shared browser code is plain ESM so Playwright can inject the same source without a build step.
// @ts-expect-error The runtime contract is narrowed by PickerEvent and the callback below.
import { startPicker } from '../../backend/app/services/picker_overlay.js';

type PickerEvent = Record<string, unknown> & { type: 'capture' | 'cancel' | 'error' };

interface ActivePicker {
  requestId: string;
  settled: boolean;
  dispose: () => void;
  resolve: (event: PickerEvent) => void;
}

let activePicker: ActivePicker | null = null;

function finishPicker(session: ActivePicker, event: PickerEvent): void {
  if (session.settled) return;
  session.settled = true;
  if (activePicker === session) activePicker = null;
  session.dispose();
  session.resolve({ ...event, requestId: session.requestId });
}

function cancelPicker(reason: string, requestId?: string): boolean {
  const session = activePicker;
  if (session === null || (requestId !== undefined && session.requestId !== requestId)) return false;
  finishPicker(session, { type: 'cancel', reason });
  return true;
}

function runPicker(action: ContentAction): Promise<PickerEvent> {
  if (!action.pickerRequestId) throw new Error('page.picker 需要 pickerRequestId');
  if (action.selectionMode !== 'single' && action.selectionMode !== 'multiple') {
    throw new Error('page.picker 需要合法的 selectionMode');
  }
  cancelPicker('replaced');

  return new Promise((resolve, reject) => {
    const session: ActivePicker = {
      requestId: action.pickerRequestId as string,
      settled: false,
      dispose: () => undefined,
      resolve,
    };
    activePicker = session;
    try {
      const dispose = startPicker(
        { requestId: session.requestId, selectionMode: action.selectionMode as 'single' | 'multiple' },
        (event: PickerEvent) => finishPicker(session, event),
      );
      session.dispose = dispose;
      if (session.settled) dispose();
    } catch (error) {
      if (activePicker === session) activePicker = null;
      session.settled = true;
      reject(error);
    }
  });
}

function dispatchClick(el: Element): void {
  const rect = el.getBoundingClientRect();
  const point = { clientX: rect.x + rect.width / 2, clientY: rect.y + rect.height / 2 };
  moveCursorTo(point.clientX, point.clientY);
  pulseClickAt(point.clientX, point.clientY);
  for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
    el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, ...point }));
  }
}

// React 在 input/textarea 实例上重写了 value 的属性描述符，直连赋值会顺带把它的 _valueTracker
// 一起更新；随后派发的 input 事件里 React 比对「当前值 === tracker 值」判定没有变化，onChange
// 于是不触发，受控组件的 state 停在旧值——DOM 看着填上了，页面其实没收到，还会在下次渲染被写回。
// 走原型上的原生 setter 绕开实例描述符，tracker 才会与 DOM 不一致，React 才认这次输入。
// Vue/Angular 读 event.target.value，两种写法都通；<select> 走 React 的 change 事件通路、
// 不做值比对，所以 dispatchSelect 不需要这层。
function setNativeValue(el: HTMLInputElement | HTMLTextAreaElement, text: string): void {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  if (setter === undefined) {
    el.value = text;
    return;
  }
  setter.call(el, text);
}

function dispatchType(el: Element, text: string): void {
  if (!(el instanceof HTMLInputElement) && !(el instanceof HTMLTextAreaElement)) return;
  const rect = el.getBoundingClientRect();
  moveCursorTo(rect.x + rect.width / 2, rect.y + rect.height / 2);
  el.focus();
  setNativeValue(el, text);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

function dispatchHover(el: Element): void {
  const rect = el.getBoundingClientRect();
  const point = { clientX: rect.x + rect.width / 2, clientY: rect.y + rect.height / 2 };
  moveCursorTo(point.clientX, point.clientY);
  for (const type of ['pointermove', 'mouseover', 'mouseenter', 'mousemove']) {
    el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, ...point }));
  }
}

function dispatchSelect(el: Element, optionValue: string): string[] {
  if (!(el instanceof HTMLSelectElement)) throw new Error('browser.select 只支持 <select> 元素');
  const rect = el.getBoundingClientRect();
  moveCursorTo(rect.x + rect.width / 2, rect.y + rect.height / 2);
  el.value = optionValue;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return Array.from(el.selectedOptions).map((option) => option.value);
}

// 老框架（Element UI/iView/layui）判键用已废弃的 keyCode，而 KeyboardEvent 的 keyCode 恒 0 会识别不出按键——
// 症状隐蔽：文本填了、Enter 发了，组件却从不提交，筛选静默失效。
const KEY_CODES: Record<string, number> = {
  Enter: 13, Escape: 27, Tab: 9, Backspace: 8, Delete: 46, Space: 32,
  ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40,
  Home: 36, End: 35, PageUp: 33, PageDown: 34,
};
function legacyKeyCode(key: string): number {
  if (KEY_CODES[key] !== undefined) return KEY_CODES[key];
  if (key.length === 1) return key.toUpperCase().charCodeAt(0);
  return 0;
}

// keyCode 必须走构造参数：Blink 支持这两个 legacy init 字段、设的是原生属性；isolated world 里
// 用 defineProperty 打补丁只是 JS expando，页面 main world 监听器读到的仍是原生 keyCode（0）。

/** key 支持形如 'Enter' / 'Escape' / 'ControlOrMeta+A' 的组合，对齐 Playwright keyboard.press 的写法 */
function dispatchKey(el: Element, key: string): void {
  if (el !== document.body) {
    const rect = el.getBoundingClientRect();
    moveCursorTo(rect.x + rect.width / 2, rect.y + rect.height / 2);
  }
  const parts = key.split('+');
  // split 至少返回一个元素，末段就是主键；`?? key` 只为让类型收窄，不是真存在的兜底分支
  const mainKey = parts[parts.length - 1] ?? key;
  const modifiers = new Set(parts.slice(0, -1).map((part) => part.toLowerCase()));
  const code = legacyKeyCode(mainKey);
  const eventInit: KeyboardEventInit & { keyCode?: number; charCode?: number } = {
    key: mainKey,
    code: mainKey.length === 1 ? `Key${mainKey.toUpperCase()}` : mainKey,
    keyCode: code,
    charCode: 0,
    bubbles: true,
    cancelable: true,
    ctrlKey: modifiers.has('control') || modifiers.has('controlormeta'),
    metaKey: modifiers.has('meta') || modifiers.has('controlormeta'),
    shiftKey: modifiers.has('shift'),
    altKey: modifiers.has('alt'),
  };
  el.dispatchEvent(new KeyboardEvent('keydown', eventInit));
  el.dispatchEvent(new KeyboardEvent('keyup', eventInit));
}

// 用指针事件序列而非原生 DragEvent：dnd-kit/Sortable.js 等监听 mouse* 而非 dragstart/drop；
// 多步 pointermove 是为了让"移动超阈值才激活"的库进入拖拽态。
function dispatchDrag(source: Element, target: Element): void {
  const srcRect = source.getBoundingClientRect();
  const dstRect = target.getBoundingClientRect();
  const from = { x: srcRect.x + srcRect.width / 2, y: srcRect.y + srcRect.height / 2 };
  const to = { x: dstRect.x + dstRect.width / 2, y: dstRect.y + dstRect.height / 2 };
  const steps = 8;

  const fire = (el: Element, type: string, point: { x: number; y: number }): void => {
    const init: PointerEventInit & MouseEventInit = {
      bubbles: true,
      cancelable: true,
      clientX: point.x,
      clientY: point.y,
      button: 0,
      buttons: 1,
      pointerId: 1,
      isPrimary: true,
    };
    el.dispatchEvent(new PointerEvent(type, init));
    el.dispatchEvent(new MouseEvent(type.replace('pointer', 'mouse'), init));
  };

  moveCursorTo(from.x, from.y);
  fire(source, 'pointerdown', from);
  for (let i = 1; i <= steps; i += 1) {
    const point = { x: from.x + ((to.x - from.x) * i) / steps, y: from.y + ((to.y - from.y) * i) / steps };
    fire(target, 'pointermove', point);
  }
  fire(target, 'pointerup', to);
  moveCursorTo(to.x, to.y);
  pulseClickAt(to.x, to.y);
}

// 只有真要碰元素的动作才顺手关遮罩：关遮罩是往用户页面上点一下，而 findCloseAffordance 认的是
// 「大遮罩右上角的小可点元素」这类特征，命中的可能是弹框里别的按钮。只读动作穿透遮罩直接读 DOM，
// 替它们点一次是流程没要求过的副作用；且每个遮罩只尝试一次，用掉了真要点击的那步就没得关。
const OVERLAY_DISMISSING_ACTIONS = new Set<string>([
  'browser.click',
  'browser.fill',
  'browser.select',
  'browser.press',
  'browser.check',
  'browser.drag',
  'browser.hover',
  // CDP 可信输入的前两步：坐标必须在遮罩关掉之后再算，否则算出来的点仍落在遮罩上。
  'scrollIntoView',
  'resolveRect',
]);

const documentId = crypto.randomUUID();

async function handleAction(action: ContentAction): Promise<unknown> {
  if (action.documentId !== undefined && action.documentId !== documentId) {
    throw new Error('文档已导航，旧引用失效，请重新 inspect_page');
  }
  const exploring = action.explorationTabId !== undefined || action.type.startsWith('page.');
  if (exploring && action.type.startsWith('browser.') && action.type !== 'browser.scroll') {
    const el = resolveElement(action);
    if (!isVisible(el)) throw new Error('目标元素不可见');
    if ((el as HTMLInputElement).disabled || el.getAttribute('aria-disabled') === 'true') throw new Error('目标元素已禁用');
    if (action.type === 'browser.fill') {
      if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) throw new Error('目标不支持文本输入');
      if (el.readOnly) throw new Error('目标元素只读');
      if (el instanceof HTMLInputElement && el.type === 'password') throw new Error('凭据输入必须通过流程凭据变量完成');
    }
  }
  markAutomationActivity();
  if (!exploring && OVERLAY_DISMISSING_ACTIONS.has(action.type)) {
    dismissBlockingOverlays();
  }
  if (!exploring && action.type !== 'takeover.show' && action.type !== 'takeover.hide' && action.type !== 'automation.pageBlock') {
    setPageBlocked(true);
  }
  switch (action.type) {
    case 'query':
      return captureSnapshot();
    case 'find': {
      if (action.query === undefined) throw new Error('find 需要 query');
      return findElements(action.query, action.limit ?? 10);
    }
    case 'browser.click': {
      dispatchClick(resolveElement(action));
      return { ok: true };
    }
    case 'browser.fill': {
      if (action.inputValue === undefined) throw new Error('browser.fill 需要 inputValue');
      dispatchType(resolveElement(action), action.inputValue);
      return { ok: true };
    }
    case 'browser.extract': {
      return dispatchExtract(action);
    }
    case 'browser.hover': {
      dispatchHover(resolveElement(action));
      return { ok: true };
    }
    case 'browser.select': {
      if (action.inputValue === undefined) throw new Error('browser.select 需要 inputValue');
      const selected = dispatchSelect(resolveElement(action), action.inputValue);
      return { selected };
    }
    case 'browser.press': {
      if (action.inputValue === undefined) throw new Error('browser.press 需要 inputValue');
      const target = action.ref !== undefined || action.selector !== undefined ? resolveElement(action) : document.body;
      dispatchKey(target, action.inputValue);
      return { ok: true };
    }
    case 'browser.scroll': {
      if (exploring && (action.ref !== undefined || action.selector !== undefined)) {
        const target = resolveElement(action);
        if (!isVisible(target)) throw new Error('滚动目标不可见');
        target.scrollBy({ top: action.distance ?? 800, behavior: 'auto' });
      } else {
        window.scrollBy({ top: action.distance ?? 800, behavior: 'auto' });
      }
      return { ok: true };
    }
    case 'browser.check': {
      const el = resolveElement(action);
      if (!(el instanceof HTMLInputElement) || (el.type !== 'checkbox' && el.type !== 'radio')) {
        throw new Error('browser.check 只支持 checkbox/radio 类型的 input');
      }
      const shouldCheck = action.checked ?? true;
      if (el.checked !== shouldCheck) dispatchClick(el);
      return { checked: el.checked };
    }
    case 'browser.drag': {
      const source = resolveElement(action);
      if (action.targetRef === undefined && action.targetSelector === undefined) {
        throw new Error('browser.drag 需要 targetRef 或 targetSelector');
      }
      const target = resolveElement({ ref: action.targetRef, selector: action.targetSelector });
      dispatchDrag(source, target);
      return { ok: true };
    }
    case 'browser.elementState': {
      const el = tryResolveElement(action);
      if (el === null) return { exists: false, hidden: true, disabled: true };
      const disabled =
        (el as HTMLButtonElement | HTMLInputElement).disabled === true || el.getAttribute('aria-disabled') === 'true';
      return { exists: true, hidden: !isVisible(el), disabled };
    }
    case 'browser.extractAll': {
      if (action.selector === undefined) throw new Error('browser.extractAll 需要 selector');
      return dispatchExtractAll(action.selector);
    }
    case 'browser.ensureLogin': {
      const loggedInProbe = action.selector;
      const loggedOutProbe = action.targetSelector;
      if (loggedInProbe !== undefined && probeSelectorVisible(loggedInProbe)) return { status: 'logged_in' };
      if (loggedOutProbe !== undefined && probeSelectorVisible(loggedOutProbe)) return { status: 'login_required' };
      if (loggedInProbe !== undefined) return { status: 'login_required' };
      const url = window.location.href.toLowerCase();
      if (url.includes('login') || url.includes('signin') || url.includes('passport')) return { status: 'login_required' };
      if (probeSelectorVisible("input[type='password']")) return { status: 'login_required' };
      return { status: 'logged_in' };
    }
    case 'scrollIntoView': {
      resolveElement(action).scrollIntoView({ block: 'center' });
      return { ok: true };
    }
    case 'resolveRect': {
      // 供 background 的 CDP 可信输入用：视口相对坐标，调用方须先 scrollIntoView，否则点击落空。
      const rect = resolveElement(action).getBoundingClientRect();
      moveCursorTo(rect.x + rect.width / 2, rect.y + rect.height / 2);
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
    }
    case 'highlight': {
      highlightElement(resolveElement(action));
      return { ok: true };
    }
    case 'automation.activity': {
      markAutomationActivity();
      return { ok: true };
    }
    case 'automation.pageBlock': {
      setPageBlocked(action.blocked ?? true);
      return { blocked: action.blocked ?? true };
    }
    case 'automation.pointer': {
      if (typeof action.x !== 'number' || typeof action.y !== 'number') {
        throw new Error('automation.pointer 需要 x 和 y');
      }
      moveCursorTo(action.x, action.y);
      if (action.pulse === true) {
        pulseClickAt(action.x, action.y);
      }
      return { ok: true };
    }
    case 'page.observe': {
      return { ...observePage({ scope: action.scope, version: action.observationVersion, includeHtml: action.includeHtml }), document_id: documentId };
    }
    case 'page.picker': {
      return runPicker(action);
    }
    case 'page.pickerCancel': {
      if (!action.pickerRequestId) throw new Error('page.pickerCancel 需要 pickerRequestId');
      return { ok: true, cancelled: cancelPicker('cancelled', action.pickerRequestId) };
    }
    case 'page.end': {
      cancelPicker('page_ended');
      setPageBlocked(false);
      return { ok: true };
    }
    case 'page.waitFor': {
      if (!action.selector) throw new Error('page.waitFor 需要 selector');
      const deadline = Date.now() + 10000;
      do {
        if (querySelectorAllDeep(action.selector).some(isVisible)) return { status: 'satisfied' };
        await new Promise(resolve => setTimeout(resolve, 100));
      } while (Date.now() < deadline);
      return { status: 'timed_out' };
    }
    case 'page.effectSignature': {
      return EFFECT_SIGNATURE();
    }
    case 'page.targetState': {
      // 两个定位字段都没给 = 整页滚动，没有元素可回读，交给脚本自己取文档滚动容器。
      const el = action.ref === undefined && action.selector === undefined ? null : tryResolveElement(action);
      return TARGET_STATE(el);
    }
    case 'page.resolveTarget': {
      // 只报事实（命中几个、编号是什么），措辞和 required_action 由后端统一给：
      // 两条通道各写一份错误文案，模型看到的失败原因就会因通道而异。
      if (action.ref !== undefined) {
        resolveElement(action);
        return { matches: 1, element_ref: action.ref };
      }
      if (action.selector === undefined) throw new Error('page.resolveTarget 需要 ref 或 selector');
      const found = querySelectorAllDeep(action.selector);
      const only = found.length === 1 ? found[0] : undefined;
      if (only === undefined) return { matches: found.length };
      return { matches: 1, element_ref: registerActionTarget(only) };
    }
    case 'takeover.show': {
      if (action.message === undefined || action.taskId === undefined) throw new Error('takeover.show 需要 message 和 taskId');
      showTakeoverBanner(action.message, action.taskId);
      setPageBlocked(false);
      return { ok: true };
    }
    case 'takeover.hide': {
      hideTakeoverBanner();
      setPageBlocked(false);
      return { ok: true };
    }
    default:
      throw new Error(`未知 action 类型: ${(action as { type: string }).type}`);
  }
}

export default defineContentScript({
  matches: ['<all_urls>'],
  main() {
    window.addEventListener('pagehide', () => cancelPicker('page_navigated'), { capture: true });
    browser.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (typeof message !== 'object' || message === null || message.source !== 'rpa-studio-bridge') {
        return undefined;
      }
      handleAction(message.action as ContentAction)
        .then((result) => sendResponse({ ok: true, result }))
        .catch((error: unknown) => sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) }));
      return true;
    });
  },
});
