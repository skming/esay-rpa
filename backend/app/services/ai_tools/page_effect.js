/**
 * 操作前后的取证脚本：Playwright 与 Chrome 扩展两条通道唯一的来源。
 *
 * 与 page_probe.js 同理：两边各写一份，同一个动作在两条通道上会得出不同的
 * action_effect，而「动作有没有生效」正是模型据以决定改不改流程的那个判断。
 * 约束同 page_probe.js：不 import、除 export 行外无模块语法、不用 TS 语法。
 */
export const EFFECT_SIGNATURE = () => {
  const q = (s) => { try { return document.querySelectorAll(s).length; } catch (e) { return -1; } };
  const kids = document.body ? Array.from(document.body.children) : [];
  const layers = kids.filter((el) => {
    const st = getComputedStyle(el);
    return (st.position === 'absolute' || st.position === 'fixed') && st.display !== 'none' && st.visibility !== 'hidden';
  }).length;
  const a = document.activeElement;
  // 可见文字的指纹：翻页只换一行内容时，元素数、浮层数、选项数全都不变，只有它能证明
  // 「页面真的换了内容」。少了它，一次成功的翻页会被报成没有可观测变化，模型于是去换元素、加等待。
  const t = (document.body ? document.body.innerText : '') || '';
  let h = 0;
  for (let i = 0; i < t.length && i < 20000; i++) h = (h * 31 + t.charCodeAt(i)) | 0;
  return {
    url: location.href,
    elements: q('*'),
    options: q('[role=option], [role=gridcell], li, td'),
    layers: layers,
    active: a ? (a.tagName.toLowerCase() + (a.id ? '#' + a.id : '')) : null,
    text_hash: h,
  };
};

export const TARGET_STATE = (el) => {
  const t = el || document.scrollingElement || document.body;
  if (!t || t.nodeType !== 1) return {};
  const st = {tag: t.tagName.toLowerCase(), focused: document.activeElement === t};
  if ('value' in t && t.type !== 'password') st.value = t.value == null ? null : String(t.value);
  if (typeof t.checked === 'boolean') st.checked = t.checked;
  if (t.tagName === 'SELECT') {
    const picked = t.selectedOptions ? Array.from(t.selectedOptions) : [];
    st.selected = picked.map((o) => o.value);
    st.selected_text = picked.map((o) => (o.textContent || '').trim());
  }
  st.scroll = {top: Math.round(t.scrollTop), left: Math.round(t.scrollLeft),
               max: Math.max(0, Math.round(t.scrollHeight - t.clientHeight))};
  // 只读标准 aria 状态：展开/选中/按下是 click 真正的目标状态。按 class 猜 is-active 这类
  // 框架约定换个组件库就判错，而判错的方向是「宣布成功」。
  for (const a of ['aria-expanded', 'aria-checked', 'aria-pressed', 'aria-selected']) {
    const v = t.getAttribute(a);
    if (v !== null) st[a.slice(5)] = v;
  }
  st.text = ((t.innerText || '').trim()).slice(0, 80);
  return st;
};

export const SETTLE_AFTER_ACTION = async (args) => {
  const started = performance.now();
  const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
  const frames = async () => {
    await frame();
    await frame();
  };
  const elapsed = () => Math.round(performance.now() - started);
  const action = args && args.action;
  const ref = args && args.ref;
  const reg = window.__rpaProbe;
  const index = typeof ref === 'string' && /^e\d+$/.test(ref) ? Number(ref.slice(1)) : -1;
  const el = reg && index >= 0 ? reg.els[index] : null;

  // 文本输入触发的联想列表通常在一次或多次异步渲染后出现。只有带 combobox 语义的输入框
  // 才等候选项，普通输入只等两帧，避免每个动作都付固定延迟。
  const controlsPopup = action === 'fill' && el && (
    el.getAttribute('role') === 'combobox'
    || el.hasAttribute('aria-controls')
    || el.hasAttribute('aria-owns')
    || el.getAttribute('aria-autocomplete') !== null
  );
  if (controlsPopup) {
    const ids = [el.getAttribute('aria-controls'), el.getAttribute('aria-owns')]
      .filter(Boolean).flatMap((value) => value.split(/\s+/));
    const hasVisibleOption = () => {
      const roots = ids.map((id) => document.getElementById(id)).filter(Boolean);
      const candidates = roots.length
        ? roots.flatMap((root) => Array.from(root.querySelectorAll('[role=option]')))
        : Array.from(document.querySelectorAll('[role=option]'));
      return candidates.some((option) => {
        const style = getComputedStyle(option);
        const rect = option.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden'
          && Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
      });
    };
    const deadline = performance.now() + 800;
    do {
      if (hasVisibleOption()) return { reason: 'options_visible', elapsed_ms: elapsed() };
      await frame();
    } while (performance.now() < deadline);
    return { reason: 'frames', elapsed_ms: elapsed() };
  }

  await frames();
  return { reason: 'frames', elapsed_ms: elapsed() };
};
