// window 级 keydown 监听的目标豁免判定，由 Studio 全局键与画布工具键共用。
// 拆成两份实现过一次，结果只有其中一份补上了新的豁免，另一份继续把按键抢走——
// 快捷键抢错目标不会报错，只表现为「这个按钮点不动 / 输入框吞字」，很难被联想到快捷键。

/** 焦点在文本输入里：按键属于输入内容，不是快捷键 */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tagName = target.tagName.toLowerCase();
  return tagName === 'input' || tagName === 'textarea' || tagName === 'select' || target.isContentEditable;
}

// 浏览器是在 keydown 未被 preventDefault 时才为按钮/链接合成 click 的。window 级监听抢下
// Enter/Space 等于让全应用的控件都无法用键盘触发，只靠鼠标的人看不出异常，键盘与读屏用户
// 则是彻底点不动。判据不看 tabindex：React Flow 给每个节点都加了 tabindex="0"，
// 按它豁免会把「选中节点后按 Enter 打开属性」这类画布快捷键一并禁掉。
const INTERACTIVE_SELECTOR = [
  'button',
  'a[href]',
  'summary',
  '[role="button"]',
  '[role="link"]',
  '[role="menuitem"]',
  '[role="menuitemcheckbox"]',
  '[role="menuitemradio"]',
  '[role="option"]',
  '[role="tab"]',
  '[role="checkbox"]',
  '[role="radio"]',
  '[role="switch"]',
].join(',');

/** 焦点在「Enter/Space 即激活」的控件上，这两个键必须留给它 */
export function isInteractiveTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  return target.closest(INTERACTIVE_SELECTOR) !== null;
}

// Radix 的对话框与菜单把焦点锁在浮层内，但 keydown 照样冒泡到 window：不让路的话，
// 删除确认框上按 Enter 会先在这里被 preventDefault 掉、确认按钮永不触发，按 b 则是在
// 被浮层挡住、什么反馈都看不到的画布上悄悄切了断点。
// 必须按 role 过滤而不是只看 data-state="open"：tooltip 同样带这个属性，而鼠标停在画布
// 任一控件上就会开一个，拿它当「有浮层」会让快捷键随机失效。
const OVERLAY_SELECTOR = ['dialog', 'alertdialog', 'menu', 'listbox']
  .map((role) => `[role="${role}"][data-state="open"]`)
  .join(',');

/** 有对话框或菜单正拿着键盘 */
export function hasOpenOverlay(): boolean {
  return document.querySelector(OVERLAY_SELECTOR) !== null;
}
