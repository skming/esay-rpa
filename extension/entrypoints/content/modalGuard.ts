// 营销/入驻类全屏弹框会挡住待操作元素，这里保守识别 + 关闭：只处理「fixed/absolute + 高 z-index + 覆盖视口 85%+」
// 的遮罩，避免误伤侧栏/吸顶导航；优先点关闭按钮、找不到退化为 Escape；每个遮罩只试一次（WeakSet）。
import { BREATHING_ID, CURSOR_ID, STATUS_ID, TAKEOVER_BANNER_ID } from './automationStyle';
import { isVisible } from './dom';

const AUTOMATION_IDS = new Set([CURSOR_ID, BREATHING_ID, STATUS_ID, TAKEOVER_BANNER_ID]);

const CLOSE_SELECTOR = ['[aria-label="close" i]', '[aria-label*="close" i]', '[aria-label*="关闭"]', '[class*="close" i]'].join(
  ','
);

const attemptedOverlays = new WeakSet<Element>();

function isOwnedByAutomation(el: Element): boolean {
  let node: Element | null = el;
  while (node !== null) {
    if (AUTOMATION_IDS.has(node.id)) return true;
    node = node.parentElement;
  }
  return false;
}

function isFullscreenMask(el: HTMLElement): boolean {
  if (isOwnedByAutomation(el)) return false;
  const style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
  if (style.position !== 'fixed' && style.position !== 'absolute') return false;
  const zIndex = Number(style.zIndex);
  if (!Number.isFinite(zIndex) || zIndex < 50) return false;
  const rect = el.getBoundingClientRect();
  if (rect.width < window.innerWidth * 0.85 || rect.height < window.innerHeight * 0.85) return false;
  return true;
}

// 遮罩多是 portal 挂到 body 的晚插入节点，浅层遍历即可，省掉整树扫描开销。
function collectOverlayCandidates(): HTMLElement[] {
  const result: HTMLElement[] = [];
  const walk = (node: Element, remaining: number): void => {
    for (const child of Array.from(node.children)) {
      if (!(child instanceof HTMLElement) || isOwnedByAutomation(child)) continue;
      if (isFullscreenMask(child)) {
        result.push(child);
        continue;
      }
      if (remaining > 0) walk(child, remaining - 1);
    }
  };
  walk(document.body, 3);
  return result;
}

// 哈希类名站点的关闭按钮常是纯图标（空叶子、无 aria-label/文案），class/文案规则都命中不了，
// 只能靠 cursor:pointer + 右上角惯例位置识别。
function isIconCloseCandidate(el: HTMLElement): boolean {
  if (el.children.length > 0) return false;
  if ((el.textContent?.trim() ?? '') !== '') return false;
  const rect = el.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0 || rect.width > 40 || rect.height > 40) return false;
  const ratio = rect.width / rect.height;
  if (ratio < 0.5 || ratio > 2) return false;
  if (window.getComputedStyle(el).cursor !== 'pointer') return false;
  return rect.top <= window.innerHeight * 0.4 && rect.left >= window.innerWidth * 0.5;
}

function findCloseAffordance(maskRect: DOMRect): HTMLElement | null {
  const bySelector = Array.from(document.querySelectorAll<HTMLElement>(CLOSE_SELECTOR));
  const bySymbol = Array.from(document.querySelectorAll<HTMLElement>('button, span, i, div, a')).filter((el) => {
    const text = el.textContent?.trim() ?? '';
    return text === '×' || text === '✕' || text === 'X' || text === '关闭';
  });
  const byIcon = Array.from(document.querySelectorAll<HTMLElement>('div, span, i')).filter(isIconCloseCandidate);
  const candidates = [...bySelector, ...bySymbol, ...byIcon];
  return (
    candidates.find((el) => {
      if (isOwnedByAutomation(el) || !isVisible(el)) return false;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0 || rect.width > 56 || rect.height > 56) return false;
      return (
        rect.left >= maskRect.left - 4 &&
        rect.top >= maskRect.top - 4 &&
        rect.right <= maskRect.right + 4 &&
        rect.bottom <= maskRect.bottom + 4
      );
    }) ?? null
  );
}

/** 返回是否尝试过关闭动作（不保证真的关掉了，调用方无需依赖返回值改变行为） */
export function dismissBlockingOverlays(): boolean {
  const masks = collectOverlayCandidates().filter((el) => !attemptedOverlays.has(el));
  const mask = masks[masks.length - 1];
  if (mask === undefined) return false;
  attemptedOverlays.add(mask);

  const closeButton = findCloseAffordance(mask.getBoundingClientRect());
  if (closeButton !== null) {
    closeButton.click();
    return true;
  }
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true }));
  return true;
}
