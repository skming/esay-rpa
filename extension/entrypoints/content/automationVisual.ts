import {
  AUTOMATION_Z_INDEX,
  CURSOR_ID,
  IDLE_HIDE_MS,
  PAGE_BLOCKER_ID,
  STATUS_ID,
  ensureAutomationStyle,
} from './automationStyle';

const CURSOR_WIDTH = 20;
const CURSOR_HEIGHT = 24;
const CURSOR_EASE = 0.34;
const TRAIL_MIN_DISTANCE = 48;
const TRAIL_MAX_DOTS = 3;

let cursorTargetX = 0;
let cursorTargetY = 0;
let cursorCurrentX = 0;
let cursorCurrentY = 0;
let cursorFrame: number | null = null;
let cursorInitialized = false;

let cursorEl: HTMLDivElement | null = null;
function ensureCursor(): HTMLDivElement {
  if (cursorEl !== null && cursorEl.isConnected) return cursorEl;
  ensureAutomationStyle();
  const el = document.createElement('div');
  el.id = CURSOR_ID;
  el.style.cssText =
    `position:fixed;z-index:${AUTOMATION_Z_INDEX + 7};pointer-events:none;left:0;top:0;width:${CURSOR_WIDTH}px;height:${CURSOR_HEIGHT}px;` +
    'opacity:0;will-change:transform,opacity;filter:drop-shadow(0 5px 8px rgba(37,99,235,0.2));' +
    'transition:opacity 120ms ease-out;';
  el.innerHTML =
    '<svg width="20" height="24" viewBox="0 0 20 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" style="animation:rpa-studio-cursor-core 1.6s ease-in-out infinite;transform-origin:4px 4px;">' +
    '<path d="M3.2 2.8L17.1 16.1L11.1 16.9L14.8 23L11.6 24L8.1 17.7L3.2 21.9V2.8Z" fill="#F8FAFC" stroke="#2563EB" stroke-width="1.45" stroke-linejoin="round"/>' +
    '<path d="M6.4 8.1V16.5L8.5 14.7L11.6 20L12.4 19.7L9.5 14.4L13.1 13.9L6.4 8.1Z" fill="#DBEAFE"/>' +
    '</svg>' +
    // 小圆点标记实际交互坐标，弱化箭头尖端指向的歧义。
    '<span class="rpa-studio-cursor-hotspot" style="position:absolute;left:3px;top:3px;width:5px;height:5px;' +
    'border-radius:9999px;background:#2563eb;transform:translate(-50%,-50%);' +
    'animation:rpa-studio-live-dot 1.6s ease-in-out infinite;"></span>';
  document.documentElement.append(el);
  cursorEl = el;
  return el;
}

let statusEl: HTMLDivElement | null = null;
let statusIconEl: HTMLSpanElement | null = null;
let statusTitleEl: HTMLSpanElement | null = null;
let statusDetailEl: HTMLSpanElement | null = null;
function ensureStatus(): HTMLDivElement {
  if (statusEl !== null && statusEl.isConnected) return statusEl;
  ensureAutomationStyle();
  const el = document.createElement('div');
  el.id = STATUS_ID;
  el.setAttribute('role', 'status');
  el.setAttribute('aria-live', 'polite');
  el.setAttribute('aria-atomic', 'true');
  el.style.cssText =
    `position:fixed;right:16px;bottom:16px;z-index:${AUTOMATION_Z_INDEX + 6};pointer-events:none;opacity:0;` +
    'box-sizing:border-box;display:flex;align-items:center;gap:8px;width:min(232px,calc(100vw - 32px));min-height:44px;' +
    'padding:7px 9px;border-radius:10px;border:1px solid rgba(148,163,184,0.3);' +
    'background:rgba(255,255,255,0.98);color:#0f172a;' +
    'font-family:"Inter Variable","PingFang SC",system-ui,sans-serif;' +
    'box-shadow:0 10px 28px rgba(15,23,42,0.12),0 1px 2px rgba(15,23,42,0.08);' +
    'transform:translate3d(0,4px,0);transition:opacity 140ms ease-out,transform 180ms cubic-bezier(0.16,1,0.3,1);';
  const icon = document.createElement('span');
  icon.setAttribute('aria-hidden', 'true');
  icon.style.cssText =
    'display:flex;align-items:center;justify-content:center;flex:0 0 auto;width:26px;height:26px;border-radius:8px;' +
    'background:#eff6ff;color:#2563eb;box-shadow:inset 0 0 0 1px rgba(37,99,235,0.14);';
  const copy = document.createElement('span');
  copy.style.cssText = 'display:flex;min-width:0;flex:1;flex-direction:column;gap:2px;';
  const title = document.createElement('span');
  title.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11.5px;font-weight:600;line-height:1.25;color:#1e293b;';
  const detail = document.createElement('span');
  detail.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px;font-weight:400;line-height:1.3;color:#64748b;';
  copy.append(title, detail);
  const dot = document.createElement('span');
  dot.className = 'rpa-studio-live-dot';
  dot.style.cssText =
    'flex:0 0 auto;width:6px;height:6px;border-radius:9999px;background:#3b82f6;animation:rpa-studio-live-dot 1.6s ease-in-out infinite;';
  el.append(icon, copy, dot);
  document.documentElement.append(el);
  statusEl = el;
  statusIconEl = icon;
  statusTitleEl = title;
  statusDetailEl = detail;
  updateStatusCopy(false);
  return el;
}

function updateStatusCopy(blocked: boolean): void {
  if (statusIconEl === null || statusTitleEl === null || statusDetailEl === null) return;
  statusTitleEl.textContent = blocked ? 'Easy RPA 已接管页面' : 'Easy RPA 正在执行';
  statusDetailEl.textContent = blocked ? '页面操作已暂时锁定' : '正在操作当前页面';
  statusIconEl.innerHTML = blocked
    ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/><path d="M12 14v2"/></svg>'
    : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 3 13 9-6 1-3 6-4-16Z"/><path d="m12 13 5 5"/></svg>';
}

function showStatus(blocked: boolean): void {
  const status = ensureStatus();
  updateStatusCopy(blocked);
  status.style.opacity = '1';
  status.style.transform = 'translate3d(0,0,0)';
}

function hideStatus(): void {
  if (statusEl === null) return;
  statusEl.style.opacity = '0';
  statusEl.style.transform = 'translate3d(0,4px,0)';
}

const PAGE_BLOCKED_CLASS = 'rpa-studio-page-blocked';
const BLOCKED_TRUSTED_EVENTS = [
  'click',
  'dblclick',
  'contextmenu',
  'mousedown',
  'mouseup',
  'pointerdown',
  'pointerup',
  'pointermove',
  'touchstart',
  'touchmove',
  'touchend',
  'keydown',
  'keyup',
  'beforeinput',
  'input',
  'wheel',
] as const;

let pageBlocked = false;
let blockListenersInstalled = false;
let pageBlockerEl: HTMLDivElement | null = null;

function ensurePageBlocker(): HTMLDivElement {
  if (pageBlockerEl !== null && pageBlockerEl.isConnected) return pageBlockerEl;
  ensureAutomationStyle();
  const el = document.createElement('div');
  el.id = PAGE_BLOCKER_ID;
  el.setAttribute('aria-hidden', 'true');
  el.style.cssText =
    `position:fixed;inset:0;z-index:${AUTOMATION_Z_INDEX + 3};display:none;` +
    'pointer-events:auto;background:transparent;cursor:not-allowed;touch-action:none;';
  document.documentElement.append(el);
  pageBlockerEl = el;
  return el;
}

function isConfirmationBannerTarget(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest(`#${CSS.escape('rpa-studio-confirmation-banner')}`) !== null;
}

function preventTrustedUserEvent(event: Event): void {
  // 自动化事件 isTrusted=false 放行；用户真实输入 isTrusted=true 在运行态阻断，避免和流程抢页面状态。
  if (!pageBlocked || !event.isTrusted || isConfirmationBannerTarget(event.target)) return;
  event.preventDefault();
  event.stopImmediatePropagation();
}

function ensureBlockListeners(): void {
  if (blockListenersInstalled) return;
  blockListenersInstalled = true;
  for (const eventName of BLOCKED_TRUSTED_EVENTS) {
    document.addEventListener(eventName, preventTrustedUserEvent, { capture: true, passive: false });
  }
}

// 运行中禁止用户操作页面本体，仅在敏感操作确认横幅出现时放开。
export function setPageBlocked(blocked: boolean): void {
  ensureAutomationStyle();
  ensureBlockListeners();
  pageBlocked = blocked;
  document.documentElement.classList.toggle(PAGE_BLOCKED_CLASS, blocked);
  ensurePageBlocker().style.display = blocked ? 'block' : 'none';
  if (blocked) {
    showStatus(true);
  } else {
    hideStatus();
    if (cursorEl !== null) cursorEl.style.opacity = '0';
  }
}

let idleHideTimer: ReturnType<typeof setTimeout> | null = null;

// 执行中的接管状态持续显示到 pageBlock=false；页面探索等非接管动作仅短暂提示。
export function markAutomationActivity(): void {
  showStatus(pageBlocked);

  if (idleHideTimer !== null) clearTimeout(idleHideTimer);
  idleHideTimer = setTimeout(() => {
    if (pageBlocked) return;
    hideStatus();
    if (cursorEl !== null) cursorEl.style.opacity = '0';
  }, IDLE_HIDE_MS);
}

// 纯视觉反馈，不 await 任何延迟，不影响 dispatch* 系列函数的同步时序/执行速度。
export function moveCursorTo(x: number, y: number): void {
  const el = ensureCursor();
  const wasInitialized = cursorInitialized;
  const previousX = cursorTargetX;
  const previousY = cursorTargetY;
  cursorTargetX = x;
  cursorTargetY = y;
  if (!cursorInitialized) {
    cursorCurrentX = x;
    cursorCurrentY = y;
    cursorInitialized = true;
    renderCursor(el);
  } else if (cursorFrame === null) {
    cursorFrame = window.requestAnimationFrame(animateCursor);
  }
  el.style.opacity = '1';
  if (wasInitialized && Number.isFinite(previousX) && Number.isFinite(previousY)) {
    drawCursorTrail(previousX, previousY, x, y);
  }
  markAutomationActivity();
}

function renderCursor(el: HTMLDivElement): void {
  el.style.transform = `translate3d(${cursorCurrentX}px, ${cursorCurrentY}px, 0)`;
}

function animateCursor(): void {
  cursorFrame = null;
  const el = ensureCursor();
  cursorCurrentX += (cursorTargetX - cursorCurrentX) * CURSOR_EASE;
  cursorCurrentY += (cursorTargetY - cursorCurrentY) * CURSOR_EASE;
  renderCursor(el);
  if (Math.hypot(cursorTargetX - cursorCurrentX, cursorTargetY - cursorCurrentY) > 0.35) {
    cursorFrame = window.requestAnimationFrame(animateCursor);
  } else {
    cursorCurrentX = cursorTargetX;
    cursorCurrentY = cursorTargetY;
    renderCursor(el);
  }
}

function drawCursorTrail(fromX: number, fromY: number, toX: number, toY: number): void {
  const dx = toX - fromX;
  const dy = toY - fromY;
  const distance = Math.hypot(dx, dy);
  if (distance < TRAIL_MIN_DISTANCE) return;
  const dotCount = Math.min(TRAIL_MAX_DOTS, Math.max(1, Math.floor(distance / 150)));
  for (let i = 1; i <= dotCount; i += 1) {
    const t = i / (dotCount + 1);
    const dot = document.createElement('div');
    const size = 3.6 - t * 1.2;
    dot.style.cssText =
      `position:fixed;left:${fromX + dx * t}px;top:${fromY + dy * t}px;width:${size}px;height:${size}px;` +
      `z-index:${AUTOMATION_Z_INDEX + 5};pointer-events:none;border-radius:9999px;` +
      'background:rgba(59,130,246,0.28);box-shadow:0 0 6px rgba(37,99,235,0.14);' +
      'animation:rpa-studio-trail-dot 360ms cubic-bezier(0.16,1,0.3,1) forwards;';
    document.documentElement.append(dot);
    setTimeout(() => dot.remove(), 400);
  }
}

export function pulseClickAt(x: number, y: number): void {
  ensureAutomationStyle();
  const ripple = document.createElement('div');
  ripple.style.cssText =
    `position:fixed;left:${x}px;top:${y}px;z-index:${AUTOMATION_Z_INDEX + 7};pointer-events:none;` +
    'width:16px;height:16px;border-radius:50%;border:1px solid rgba(37,99,235,0.58);background:rgba(59,130,246,0.16);' +
    'box-shadow:0 0 10px rgba(59,130,246,0.18);animation:rpa-studio-ripple 280ms cubic-bezier(0.16,1,0.3,1) forwards;';
  const point = document.createElement('div');
  point.style.cssText =
    `position:fixed;left:${x}px;top:${y}px;z-index:${AUTOMATION_Z_INDEX + 8};pointer-events:none;` +
    'width:4px;height:4px;border-radius:9999px;background:#2563eb;transform:translate(-50%,-50%);' +
    'box-shadow:0 0 0 2px rgba(255,255,255,0.9),0 0 8px rgba(37,99,235,0.24);opacity:0.92;transition:opacity 140ms ease-out;';
  document.documentElement.append(ripple);
  document.documentElement.append(point);
  setTimeout(() => {
    point.style.opacity = '0';
  }, 120);
  setTimeout(() => {
    ripple.remove();
    point.remove();
  }, 320);
}

// 「高亮」就是把光标挪到元素中心，不画停留时长的高亮框：合成光标本身已经是可见反馈，
// 再叠一层框会把它盖住。调用方因此不需要传时长。
export function highlightElement(el: Element): void {
  const rect = el.getBoundingClientRect();
  moveCursorTo(rect.x + rect.width / 2, rect.y + rect.height / 2);
}
