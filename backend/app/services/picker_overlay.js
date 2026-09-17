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
    const root = host.attachShadow({ mode: 'open' });
    root.innerHTML = `
      <style>
        :host { all: initial; }
        .frame { position: fixed; inset: 0; z-index: 2147483646; pointer-events: none; }
        .toolbar { position: fixed; top: 12px; right: 12px; display: flex; align-items: center; gap: 8px;
          padding: 8px 10px; border: 1px solid rgba(129, 140, 248, .52); border-radius: 8px;
          color: #eef2ff; background: rgba(15, 23, 42, .96); box-shadow: 0 8px 24px rgba(15, 23, 42, .36);
          font: 13px/1.2 system-ui, sans-serif; pointer-events: auto; }
        button { border: 0; border-radius: 6px; padding: 5px 8px; color: inherit; background: rgba(129, 140, 248, .22);
          font: inherit; cursor: pointer; }
        button:hover { background: rgba(129, 140, 248, .38); }
        .cancel { background: #dc2626; }
        .highlight { position: fixed; display: none; box-sizing: border-box; border: 2px solid #818cf8;
          border-radius: 4px; background: rgba(129, 140, 248, .12); pointer-events: none; }
        .hint { max-width: 320px; color: #c7d2fe; }
      </style>
      <div class="frame"><div class="highlight"></div><div class="frame-guards"></div></div>
      <div class="toolbar">
        <span class="hint">${selectionMode === 'multiple' ? '拾取行或列表项' : '拾取元素'}</span>
        <button type="button" class="toggle">暂停拾取</button>
        <button type="button" class="cancel">取消</button>
      </div>`;
    document.documentElement.append(host);

    const highlight = root.querySelector('.highlight');
    const frameGuards = root.querySelector('.frame-guards');
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
        toggle.textContent = next ? '暂停拾取' : '继续拾取';
        hint.textContent = next
            ? (selectionMode === 'multiple' ? '拾取行或列表项' : '拾取元素')
            : '已暂停，可操作页面后继续拾取';
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
