import { createDomLocator } from './dom_locator.js';

export const startPicker = (options, emit) => {
    const overlayId = 'rpa-picker-overlay';
    const requestId = options && options.requestId;
    const selectionMode = options && options.selectionMode === 'multiple' ? 'multiple' : 'single';
    if (window.top !== window) return () => {};

    const previousDispose = window.__rpaPickerDispose;
    if (typeof previousDispose === 'function') previousDispose();
    document.getElementById(overlayId)?.remove();

    const host = document.createElement('div');
    host.id = overlayId;
    host.setAttribute('aria-live', 'polite');
    host.dataset.state = 'active';
    const activeTitle = selectionMode === 'multiple' ? '选择列表行' : '选择页面元素';
    const activeHint = selectionMode === 'multiple' ? '单击一条记录，自动识别同类项' : '单击目标元素 · Esc 退出';
    const root = host.attachShadow({ mode: 'open' });
    root.innerHTML = `
      <style>
        :host { all: initial; color-scheme: light; }
        *, *::before, *::after { box-sizing: border-box; }
        .frame { position: fixed; inset: 0; z-index: 2147483646; pointer-events: none; }
        .toolbar { position: fixed; top: 16px; right: 16px; z-index: 2147483647; pointer-events: auto;
          display: grid; grid-template-columns: 28px minmax(0, 1fr) auto; align-items: center; gap: 10px;
          width: min(390px, calc(100vw - 32px)); min-height: 52px; padding: 8px 9px 8px 10px;
          border: 1px solid rgba(148, 163, 184, .32); border-radius: 12px; background: rgba(255, 255, 255, .98);
          color: #0f172a; box-shadow: 0 12px 32px rgba(15, 23, 42, .14), 0 1px 2px rgba(15, 23, 42, .08);
          font-family: "Inter Variable", "PingFang SC", system-ui, sans-serif; }
        .mode-icon { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px;
          border-radius: 8px; color: #2563eb; background: #eff6ff; box-shadow: inset 0 0 0 1px rgba(37, 99, 235, .14); }
        .copy { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
        .title, .hint { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .title { color: #1e293b; font-size: 12px; font-weight: 600; line-height: 1.25; }
        .hint { color: #64748b; font-size: 10.5px; font-weight: 400; line-height: 1.3; }
        .actions { display: flex; align-items: center; gap: 4px; }
        button { height: 28px; border: 0; border-radius: 7px; padding: 0 9px; color: #334155; background: transparent;
          font: 500 11px/1 "Inter Variable", "PingFang SC", system-ui, sans-serif; cursor: pointer; }
        button:hover { background: #f1f5f9; color: #0f172a; }
        button:focus-visible { outline: 2px solid #2563eb; outline-offset: 2px; }
        .toggle { background: #eef2ff; color: #3730a3; }
        .toggle:hover { background: #e0e7ff; color: #312e81; }
        .cancel:hover { background: #fef2f2; color: #b91c1c; }
        .highlight { position: fixed; display: none; border: 2px solid #2563eb;
          background: rgba(37, 99, 235, .07); box-shadow: 0 0 0 3px rgba(37, 99, 235, .14), 0 8px 20px rgba(37, 99, 235, .12);
          pointer-events: none; }
        .highlight-label { position: absolute; left: -2px; bottom: calc(100% + 6px); max-width: min(360px, calc(100vw - 24px));
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 4px 7px; border-radius: 5px;
          background: #2563eb; color: #fff; box-shadow: 0 4px 12px rgba(37, 99, 235, .2);
          font: 500 10px/1.2 "JetBrains Mono Variable", ui-monospace, SFMono-Regular, Menlo, monospace; }
        .highlight[data-label-align="right"] .highlight-label { right: -2px; left: auto; }
        .highlight[data-label-position="below"] .highlight-label { top: calc(100% + 6px); bottom: auto; }
        :host([data-state="paused"]) .mode-icon { color: #92400e; background: #fffbeb;
          box-shadow: inset 0 0 0 1px rgba(217, 119, 6, .2); }
        :host([data-state="paused"]) .toggle { background: #0f172a; color: #fff; }
        :host([data-state="paused"]) .toggle:hover { background: #1e293b; color: #fff; }
        @media (max-width: 520px) {
          .toolbar { top: 10px; right: 10px; width: calc(100vw - 20px); }
          .hint { display: none; }
        }
        @media (prefers-reduced-motion: reduce) {
          .toolbar, .highlight { transition: none !important; }
        }
      </style>
      <div class="frame"><div class="highlight"><span class="highlight-label"></span></div><div class="frame-guards"></div></div>
      <div class="toolbar" role="region" aria-label="Easy RPA 元素拾取器">
        <span class="mode-icon" aria-hidden="true">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/><path d="M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M19.07 4.93l-2.83 2.83M7.76 16.24l-2.83 2.83"/>
          </svg>
        </span>
        <span class="copy"><strong class="title">${activeTitle}</strong><span class="hint">${activeHint}</span></span>
        <span class="actions">
          <button type="button" class="toggle" aria-pressed="false">暂停</button>
          <button type="button" class="cancel">退出</button>
        </span>
      </div>`;
    document.documentElement.append(host);

    const highlight = root.querySelector('.highlight');
    const highlightLabel = root.querySelector('.highlight-label');
    const frameGuards = root.querySelector('.frame-guards');
    const title = root.querySelector('.title');
    const hint = root.querySelector('.hint');
    const toggle = root.querySelector('.toggle');
    const cancel = root.querySelector('.cancel');
    let picking = true;
    let disposed = false;
    const frameDocuments = new Map();
    const frameLoadListeners = new Map();
    const frameObserver = new MutationObserver(() => syncFrames());

    function deliver(event) {
        try {
            const pending = emit(event);
            if (pending && typeof pending.catch === 'function') pending.catch(() => {});
        } catch (e) {}
    }

    function selectedFrom(event) {
        return event.composedPath().find(node => node instanceof Element) || null;
    }

    function isOverlayEvent(event) {
        return event.composedPath().includes(host);
    }

    function setPicking(next) {
        picking = next;
        host.dataset.state = next ? 'active' : 'paused';
        toggle.textContent = next ? '暂停' : '继续';
        toggle.setAttribute('aria-pressed', String(!next));
        title.textContent = next ? activeTitle : '拾取已暂停';
        hint.textContent = next ? activeHint : '现在可以操作页面，完成后点击继续';
        if (!next) highlight.style.display = 'none';
        frameGuards.style.display = next ? '' : 'none';
        if (next) syncFrames();
    }

    function onMove(event) {
        if (!picking || isOverlayEvent(event)) return;
        const target = selectedFrom(event);
        if (!target) return;
        const rect = target.getBoundingClientRect();
        highlight.style.display = 'block';
        highlight.style.left = `${rect.left}px`;
        highlight.style.top = `${rect.top}px`;
        highlight.style.width = `${Math.max(1, rect.width)}px`;
        highlight.style.height = `${Math.max(1, rect.height)}px`;
        highlight.dataset.labelPosition = rect.top >= 32 ? 'above' : 'below';
        const classes = Array.from(target.classList).slice(0, 4).map(name => `.${CSS.escape(name)}`).join('');
        highlightLabel.textContent = classes || (target.id ? `#${CSS.escape(target.id)}` : target.tagName.toLowerCase());
        highlight.dataset.labelAlign = rect.left + highlightLabel.getBoundingClientRect().width > window.innerWidth - 12
            ? 'right'
            : 'left';
    }

    function finish(event) {
        deliver(event);
        dispose();
    }

    function onClick(event) {
        if (!picking || isOverlayEvent(event)) return;
        const target = selectedFrom(event);
        if (!target) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        // 暂停时用户可能展开菜单或创建新的 open shadow root；此刻再建 root 集合才能验证实际页面。
        const selection = createDomLocator(document).selectionFor(target, selectionMode);
        if (!selection.ok) {
            finish({
                type: 'error', requestId: requestId, code: selection.reason || 'selector_validation_failed',
                message: selection.reason === 'frame_unsupported'
                    ? '当前拾取器不支持 iframe 内容，请回到主页面选择元素。'
                    : '无法生成已验证的元素定位，请更精确地选择目标。',
            });
            return;
        }
        finish({
            type: 'capture',
            requestId: requestId,
            selector: selection.selector,
            matches: selection.matches,
            selectedIncluded: selection.selectedIncluded,
            usesPosition: selection.usesPosition,
            text: /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) ? '' : String(target.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 160),
            url: location.href,
        });
    }

    function onKeyDown(event) {
        if (event.key !== 'Escape') return;
        event.preventDefault();
        event.stopImmediatePropagation();
        finish({ type: 'cancel', requestId: requestId });
    }

    function finishFrameUnsupported(event) {
        if (!picking) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        finish({
            type: 'error', requestId: requestId, code: 'frame_unsupported',
            message: '当前拾取器不支持 iframe 内容，请回到主页面选择元素。',
        });
    }

    function addFrameGuard(frame) {
        const rect = frame.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;
        const guard = document.createElement('div');
        guard.setAttribute('aria-label', '当前拾取器不支持 iframe 内容');
        guard.style.cssText = `position:fixed;left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px;pointer-events:auto;`;
        guard.addEventListener('click', finishFrameUnsupported, true);
        frameGuards.append(guard);
    }

    function syncFrames() {
        if (disposed) return;
        frameGuards.replaceChildren();
        for (const rootNode of createDomLocator(document).roots) {
            for (const frame of rootNode.querySelectorAll('iframe, frame')) {
                let frameDocument = null;
                try { frameDocument = frame.contentDocument; } catch (e) {}
                if (frameDocument) {
                    if (frameDocuments.get(frame) !== frameDocument) {
                        frameDocuments.get(frame)?.removeEventListener('click', finishFrameUnsupported, true);
                        frameDocument.addEventListener('click', finishFrameUnsupported, true);
                        frameDocuments.set(frame, frameDocument);
                    }
                } else {
                    addFrameGuard(frame);
                }
                if (!frameLoadListeners.has(frame)) {
                    const onLoad = () => syncFrames();
                    frame.addEventListener('load', onLoad);
                    frameLoadListeners.set(frame, onLoad);
                }
            }
        }
        frameGuards.style.display = picking ? '' : 'none';
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        window.removeEventListener('mousemove', onMove, true);
        window.removeEventListener('click', onClick, true);
        window.removeEventListener('keydown', onKeyDown, true);
        window.removeEventListener('scroll', syncFrames, true);
        window.removeEventListener('resize', syncFrames);
        frameObserver.disconnect();
        for (const [frame, frameDocument] of frameDocuments) frameDocument.removeEventListener('click', finishFrameUnsupported, true);
        for (const [frame, onLoad] of frameLoadListeners) frame.removeEventListener('load', onLoad);
        host.remove();
        if (window.__rpaPickerDispose === dispose) delete window.__rpaPickerDispose;
    }

    toggle.addEventListener('click', event => { event.stopPropagation(); setPicking(!picking); });
    cancel.addEventListener('click', event => {
        event.stopPropagation();
        finish({ type: 'cancel', requestId: requestId });
    });
    window.addEventListener('mousemove', onMove, true);
    window.addEventListener('click', onClick, true);
    window.addEventListener('keydown', onKeyDown, true);
    window.addEventListener('scroll', syncFrames, true);
    window.addEventListener('resize', syncFrames);
    frameObserver.observe(document.documentElement, { childList: true, subtree: true });
    syncFrames();
    window.__rpaPickerDispose = dispose;
    return dispose;
};
