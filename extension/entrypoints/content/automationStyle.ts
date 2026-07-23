// 自动化视觉样式：只注入 keyframes/DOM id/动效参数，行为在 automationVisual.ts，避免主流程和 CSS 字符串耦合。
export const CURSOR_ID = 'rpa-studio-cursor';
export const BREATHING_ID = 'rpa-studio-breathing';
export const STATUS_ID = 'rpa-studio-automation-status';
export const TAKEOVER_BANNER_ID = 'rpa-studio-takeover-banner';
export const PAGE_BLOCKER_ID = 'rpa-studio-page-blocker';
export const IDLE_HIDE_MS = 3200;

const AUTOMATION_STYLE_ID = 'rpa-studio-automation-style';
export const AUTOMATION_Z_INDEX = 2147483640;

export function ensureAutomationStyle(): void {
  if (document.getElementById(AUTOMATION_STYLE_ID) !== null) return;
  const style = document.createElement('style');
  style.id = AUTOMATION_STYLE_ID;
  style.textContent = `
@keyframes rpa-studio-breathe {
  0%, 100% {
    opacity: 0.58;
    box-shadow:
      inset 0 0 0 1px rgba(37,99,235,0.32),
      inset 0 0 30px rgba(59,130,246,0.18),
      inset 0 0 80px rgba(99,102,241,0.13);
  }
  50% {
    opacity: 1;
    box-shadow:
      inset 0 0 0 1px rgba(37,99,235,0.62),
      inset 0 0 56px rgba(59,130,246,0.32),
      inset 0 0 130px rgba(99,102,241,0.2);
  }
}
@keyframes rpa-studio-frame-sweep {
  0%, 100% {
    opacity: 0.32;
    transform: translate3d(-24%, 0, 0);
  }
  50% {
    opacity: 0.82;
    transform: translate3d(24%, 0, 0);
  }
}
@keyframes rpa-studio-cursor-core {
  0%, 100% { transform: translate3d(-1px, -1px, 0) rotate(-2deg) scale(1); }
  50% { transform: translate3d(-1px, -1px, 0) rotate(0deg) scale(0.99); }
}
@keyframes rpa-studio-trail-dot {
  0% { opacity: 0.18; transform: translate3d(-50%, -50%, 0) scale(1); filter: blur(0); }
  100% { opacity: 0; transform: translate3d(-50%, -50%, 0) scale(0.42); filter: blur(1px); }
}
@keyframes rpa-studio-ripple {
  0% { transform: translate3d(-50%, -50%, 0) scale(0.42); opacity: 0.54; }
  100% { transform: translate3d(-50%, -50%, 0) scale(2.35); opacity: 0; }
}
@keyframes rpa-studio-live-dot {
  0%, 100% { box-shadow: 0 0 0 3px rgba(59,130,246,0.14); }
  50% { box-shadow: 0 0 0 5px rgba(59,130,246,0.22); }
}
@keyframes rpa-studio-takeover-in {
  0% { opacity: 0; transform: translate3d(-50%, -10px, 0) scale(0.98); }
  100% { opacity: 1; transform: translate3d(-50%, 0, 0) scale(1); }
}
@keyframes rpa-studio-takeover-out {
  0% { opacity: 1; transform: translate3d(-50%, 0, 0) scale(1); }
  100% { opacity: 0; transform: translate3d(-50%, -8px, 0) scale(0.98); }
}
@keyframes rpa-studio-takeover-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(245,158,11,0.38); }
  50% { box-shadow: 0 0 0 5px rgba(245,158,11,0); }
}
/* 运行期间用透明遮罩接管鼠标/触摸命中，保留页面自身 pointer-events 语义。 */
html.rpa-studio-page-blocked > body {
  user-select: none !important;
}
@media (prefers-reduced-motion: reduce) {
  #${CURSOR_ID} { transition: opacity 120ms linear !important; }
  #${CURSOR_ID} .rpa-studio-cursor-hotspot,
  #${BREATHING_ID} .rpa-studio-frame,
  #${BREATHING_ID} .rpa-studio-frame-sweep,
  #${STATUS_ID} .rpa-studio-live-dot,
  #${TAKEOVER_BANNER_ID} { animation: none !important; }
  #${TAKEOVER_BANNER_ID} .rpa-studio-takeover-indicator {
    animation: none !important;
  }
  #${STATUS_ID} { transition: opacity 120ms linear !important; }
}
`;
  document.documentElement.append(style);
}
