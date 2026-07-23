import { AUTOMATION_Z_INDEX, TAKEOVER_BANNER_ID } from './automationStyle';

let currentTakeoverTaskId: string | null = null;

export function hideTakeoverBanner(): void {
  const banner = document.getElementById(TAKEOVER_BANNER_ID);
  currentTakeoverTaskId = null;
  if (banner === null) return;
  banner.style.animation = 'rpa-studio-takeover-out 160ms ease-in forwards';
  // reduced-motion 下 animation 被强制 none、animationend 不触发，用定时器兜底移除，避免横幅卡死在页面。
  let removed = false;
  const remove = () => {
    if (removed) return;
    removed = true;
    banner.remove();
  };
  banner.addEventListener('animationend', remove, { once: true });
  setTimeout(remove, 200);
}

export function showTakeoverBanner(message: string, taskId: string): void {
  hideTakeoverBanner();
  currentTakeoverTaskId = taskId;
  const banner = document.createElement('div');
  banner.id = TAKEOVER_BANNER_ID;
  banner.setAttribute('role', 'status');
  banner.setAttribute('aria-live', 'polite');
  banner.style.cssText =
    `position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:${AUTOMATION_Z_INDEX + 8};` +
    'box-sizing:border-box;width:min(680px,calc(100vw - 24px));min-height:42px;padding:8px 8px 8px 12px;' +
    'display:flex;align-items:center;justify-content:space-between;gap:12px;border-radius:10px;' +
    'border:1px solid rgba(217,119,6,0.34);background:rgba(255,251,235,0.96);color:#451a03;' +
    'font:13px/1.4 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;' +
    'box-shadow:0 8px 24px rgba(15,23,42,0.14),0 1px 2px rgba(15,23,42,0.08);backdrop-filter:blur(10px);' +
    'animation:rpa-studio-takeover-in 200ms ease-out;';
  const content = document.createElement('span');
  content.style.cssText = 'display:flex;align-items:flex-start;min-width:0;gap:8px;';
  const indicator = document.createElement('span');
  indicator.className = 'rpa-studio-takeover-indicator';
  indicator.style.cssText =
    'display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto;width:20px;height:20px;border-radius:6px;margin-top:1px;' +
    'background:#f59e0b;color:#1c1917;font-size:12px;font-weight:700;animation:rpa-studio-takeover-pulse 2s ease-out infinite;';
  indicator.textContent = '!';
  const text = document.createElement('span');
  text.style.cssText =
    'min-width:0;overflow:hidden;text-overflow:ellipsis;word-break:break-word;' +
    'display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;';
  text.textContent = `Easy RPA 等待人工操作：${message}`;
  content.append(indicator, text);
  const button = document.createElement('button');
  button.textContent = '完成，继续执行';
  button.style.cssText =
    'flex:0 0 auto;height:28px;border:0;border-radius:8px;padding:0 12px;cursor:pointer;' +
    'background:#0f172a;color:#fff;font:600 12px/1 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;' +
    'box-shadow:0 1px 2px rgba(15,23,42,0.18);';
  button.addEventListener('mouseenter', () => {
    button.style.background = '#1e293b';
  });
  button.addEventListener('mouseleave', () => {
    button.style.background = '#0f172a';
  });
  button.addEventListener('focus', () => {
    button.style.outline = '2px solid rgba(37,99,235,0.42)';
    button.style.outlineOffset = '2px';
  });
  button.addEventListener('blur', () => {
    button.style.outline = 'none';
  });
  button.addEventListener('click', () => {
    void browser.runtime.sendMessage({ source: 'rpa-studio-bridge-event', type: 'takeoverResume', taskId: currentTakeoverTaskId });
    hideTakeoverBanner();
  });
  banner.append(content, button);
  document.documentElement.append(banner);
}
