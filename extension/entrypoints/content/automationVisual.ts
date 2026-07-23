import {
  AUTOMATION_Z_INDEX,
  BREATHING_ID,
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
// 距上次 action 超过此间隔才判定为"等待/思考"态并点亮呼吸灯，避免连续动作时常亮。
const BREATHE_THINK_DELAY_MS = 900;

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

let breathingEl: HTMLDivElement | null = null;
function ensureBreathing(): HTMLDivElement {
  if (breathingEl !== null && breathingEl.isConnected) return breathingEl;
  ensureAutomationStyle();
  const el = document.createElement('div');
  el.id = BREATHING_ID;
  el.style.cssText =
    `position:fixed;inset:0;z-index:${AUTOMATION_Z_INDEX + 4};pointer-events:none;opacity:0;` +
    'contain:layout paint style;transition:opacity 180ms ease-out;';
  const frame = document.createElement('div');
  frame.className = 'rpa-studio-frame';
  frame.style.cssText =
    'position:absolute;inset:0;border-radius:0;' +
    'animation:rpa-studio-breathe 3.2s cubic-bezier(0.16,1,0.3,1) infinite;';
  const sweep = document.createElement('div');
  sweep.className = 'rpa-studio-frame-sweep';
  sweep.style.cssText =
    'position:absolute;left:10%;right:10%;top:0;height:2px;border-radius:9999px;' +
    'background:linear-gradient(90deg,transparent,rgba(99,102,241,0.28),rgba(59,130,246,0.58),transparent);' +
    'filter:blur(0.1px);animation:rpa-studio-frame-sweep 3.2s cubic-bezier(0.16,1,0.3,1) infinite;';
  el.append(frame, sweep);
  document.documentElement.append(el);
  breathingEl = el;
  return el;
}

let statusEl: HTMLDivElement | null = null;
function ensureStatus(): HTMLDivElement {
  if (statusEl !== null && statusEl.isConnected) return statusEl;
  ensureAutomationStyle();
  const el = document.createElement('div');
  el.id = STATUS_ID;
  el.style.cssText =
    `position:fixed;right:12px;bottom:12px;z-index:${AUTOMATION_Z_INDEX + 6};pointer-events:none;opacity:0;` +
    'display:flex;align-items:center;gap:7px;height:24px;padding:0 8px;border-radius:7px;' +
    'border:1px solid rgba(147,197,253,0.3);background:rgba(15,23,42,0.78);color:#f8fafc;' +
    'font:12px/1.2 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;' +
    'box-shadow:0 4px 12px rgba(15,23,42,0.18);backdrop-filter:blur(8px);transition:opacity 140ms ease-out;';
  const dot = document.createElement('span');
  dot.className = 'rpa-studio-live-dot';
  dot.style.cssText =
    'width:6px;height:6px;border-radius:9999px;background:#3b82f6;animation:rpa-studio-live-dot 1.6s ease-in-out infinite;';
  const text = document.createElement('span');
  text.textContent = 'Easy RPA 正在操作';
  el.append(dot, text);
  document.documentElement.append(el);
  statusEl = el;
  return el;
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

function isTakeoverBannerTarget(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest(`#${CSS.escape('rpa-studio-takeover-banner')}`) !== null;
}

function preventTrustedUserEvent(event: Event): void {
  // 自动化事件 isTrusted=false 放行；用户真实输入 isTrusted=true 在运行态阻断，避免和流程抢页面状态。
  if (!pageBlocked || !event.isTrusted || isTakeoverBannerTarget(event.target)) return;
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

// 运行中禁止用户操作页面本体，仅在人工接管横幅出现时放开。
export function setPageBlocked(blocked: boolean): void {
  ensureAutomationStyle();
  ensureBlockListeners();
  pageBlocked = blocked;
  document.documentElement.classList.toggle(PAGE_BLOCKED_CLASS, blocked);
  ensurePageBlocker().style.display = blocked ? 'block' : 'none';
}

let idleHideTimer: ReturnType<typeof setTimeout> | null = null;
let breatheShowTimer: ReturnType<typeof setTimeout> | null = null;

// 每次 action：状态徽标立即常亮、静默后淡出；呼吸光晕仅在连续 BREATHE_THINK_DELAY_MS 无新 action 才亮，
// 亮起后不随后续 action 反复熄重亮（防闪烁），真正熄灭只在整体静默 IDLE_HIDE_MS 之后。
export function markAutomationActivity(): void {
  const breathing = ensureBreathing();
  const status = ensureStatus();
  status.style.opacity = '1';

  if (breathing.style.opacity !== '1') {
    if (breatheShowTimer !== null) clearTimeout(breatheShowTimer);
    breatheShowTimer = setTimeout(() => {
      breathing.style.opacity = '1';
    }, BREATHE_THINK_DELAY_MS);
  }

  if (idleHideTimer !== null) clearTimeout(idleHideTimer);
  idleHideTimer = setTimeout(() => {
    if (breatheShowTimer !== null) clearTimeout(breatheShowTimer);
    breathing.style.opacity = '0';
    status.style.opacity = '0';
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

export function highlightElement(el: Element, _durationMs: number): void {
  const rect = el.getBoundingClientRect();
  moveCursorTo(rect.x + rect.width / 2, rect.y + rect.height / 2);
}
