const { ipcRenderer } = require('electron');

// ── Element highlight overlay ─────────────────────────────────────────────
const overlay = document.createElement('div');
overlay.style.cssText = [
  'position: fixed',
  'z-index: 2147483645',
  'pointer-events: none',
  'border: 2px solid #2563eb',
  'background: rgba(37,99,235,0.08)',
  'border-radius: 4px',
  'box-shadow: 0 0 0 9999px rgba(15,23,42,0.10)'
].join(';');

const label = document.createElement('div');
label.style.cssText = [
  'position: fixed',
  'z-index: 2147483645',
  'pointer-events: none',
  'padding: 4px 8px',
  'border-radius: 6px',
  'background: #0f172a',
  'color: white',
  'font: 11px ui-monospace, SFMono-Regular, Menlo, monospace',
  'max-width: 520px',
  'overflow: hidden',
  'text-overflow: ellipsis',
  'white-space: nowrap'
].join(';');

// ── Toolbar ───────────────────────────────────────────────────────────────
const toolbar = document.createElement('div');
toolbar.style.cssText = [
  'position: fixed',
  'top: 0',
  'left: 0',
  'right: 0',
  'z-index: 2147483647',
  'display: flex',
  'align-items: center',
  'gap: 6px',
  'padding: 6px 10px',
  'background: #0f172a',
  'box-shadow: 0 2px 8px rgba(0,0,0,0.3)',
  'font-family: ui-sans-serif, system-ui, sans-serif',
  'font-size: 12px',
  'color: white'
].join(';');

const pickBadge = document.createElement('span');
pickBadge.style.cssText = [
  'flex-shrink: 0',
  'display: inline-flex',
  'align-items: center',
  'gap: 4px',
  'padding: 2px 8px',
  'border-radius: 999px',
  'font-size: 11px',
  'font-weight: 600',
  'background: #2563eb',
  'color: white'
].join(';');
pickBadge.textContent = '● 拾取中';

const urlInput = document.createElement('input');
urlInput.type = 'text';
urlInput.style.cssText = [
  'flex: 1',
  'min-width: 0',
  'height: 26px',
  'padding: 0 8px',
  'border-radius: 6px',
  'border: 1px solid rgba(255,255,255,0.15)',
  'background: rgba(255,255,255,0.08)',
  'color: white',
  'font-size: 12px',
  'font-family: ui-monospace, SFMono-Regular, Menlo, monospace',
  'outline: none'
].join(';');
urlInput.placeholder = 'https://...';

const goBtn = document.createElement('button');
goBtn.style.cssText = [
  'flex-shrink: 0',
  'height: 26px',
  'padding: 0 10px',
  'border-radius: 6px',
  'border: 1px solid rgba(255,255,255,0.2)',
  'background: rgba(255,255,255,0.1)',
  'color: white',
  'font-size: 11px',
  'cursor: pointer',
  'white-space: nowrap'
].join(';');
goBtn.textContent = '跳转';

const toggleBtn = document.createElement('button');
toggleBtn.style.cssText = [
  'flex-shrink: 0',
  'height: 26px',
  'padding: 0 10px',
  'border-radius: 6px',
  'border: 1px solid rgba(255,255,255,0.2)',
  'background: rgba(37,99,235,0.4)',
  'color: white',
  'font-size: 11px',
  'cursor: pointer',
  'white-space: nowrap'
].join(';');
toggleBtn.textContent = '暂停拾取';

const cancelBtn = document.createElement('button');
cancelBtn.style.cssText = [
  'flex-shrink: 0',
  'height: 26px',
  'padding: 0 10px',
  'border-radius: 6px',
  'border: 1px solid rgba(239,68,68,0.4)',
  'background: rgba(239,68,68,0.2)',
  'color: #fca5a5',
  'font-size: 11px',
  'cursor: pointer',
  'white-space: nowrap'
].join(';');
cancelBtn.textContent = '关闭拾取器';

toolbar.append(pickBadge, urlInput, goBtn, toggleBtn, cancelBtn);

// ── State ─────────────────────────────────────────────────────────────────
let pickingEnabled = true;

function setPickingEnabled(enabled) {
  pickingEnabled = enabled;
  if (enabled) {
    pickBadge.textContent = '● 拾取中';
    pickBadge.style.background = '#2563eb';
    toggleBtn.textContent = '暂停拾取';
    toggleBtn.style.background = 'rgba(37,99,235,0.4)';
    overlay.style.display = '';
    label.style.display = '';
  } else {
    pickBadge.textContent = '○ 暂停';
    pickBadge.style.background = 'rgba(255,255,255,0.15)';
    toggleBtn.textContent = '开始拾取';
    toggleBtn.style.background = 'rgba(255,255,255,0.1)';
    overlay.style.display = 'none';
    label.style.display = 'none';
  }
}

// ── Toolbar interactions ──────────────────────────────────────────────────
toggleBtn.addEventListener('click', (event) => {
  event.stopPropagation();
  setPickingEnabled(!pickingEnabled);
});

cancelBtn.addEventListener('click', (event) => {
  event.stopPropagation();
  ipcRenderer.send('picker:cancel');
});

goBtn.addEventListener('click', (event) => {
  event.stopPropagation();
  const url = urlInput.value.trim();
  if (url) {
    window.location.href = url.startsWith('http') ? url : `https://${url}`;
  }
});

urlInput.addEventListener('keydown', (event) => {
  event.stopPropagation();
  if (event.key === 'Enter') {
    goBtn.click();
  }
});

urlInput.addEventListener('click', (event) => {
  event.stopPropagation();
});

// Prevent toolbar area from being counted as an element to pick
toolbar.addEventListener('click', (event) => {
  event.stopPropagation();
});

// ── DOM ready ─────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  document.documentElement.append(toolbar, overlay, label);
  urlInput.value = window.location.href;
  // Push page content down so toolbar doesn't overlap
  document.documentElement.style.marginTop = '40px';
});

window.addEventListener('load', () => {
  urlInput.value = window.location.href;
});

// ── Element highlight ─────────────────────────────────────────────────────
window.addEventListener(
  'mousemove',
  (event) => {
    if (!pickingEnabled) return;
    const element = event.target instanceof Element ? event.target : null;
    if (toolbar.contains(element)) {
      updateOverlay(null);
      return;
    }
    updateOverlay(element);
  },
  true
);

// ── Click capture ─────────────────────────────────────────────────────────
window.addEventListener(
  'click',
  (event) => {
    if (!pickingEnabled) return;
    const element = event.target instanceof Element ? event.target : null;
    if (element === null || toolbar.contains(element)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const selector = buildSelector(element);
    ipcRenderer.send('picker:capture', {
      selector,
      confidence: selector.startsWith('#') ? 0.96 : selector.includes('[data-') ? 0.92 : 0.78,
      text: normalizeText(element.textContent),
      url: window.location.href
    });
  },
  true
);

window.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    ipcRenderer.send('picker:cancel');
  }
});

// ── Overlay helpers ───────────────────────────────────────────────────────
function updateOverlay(element) {
  if (
    element === null ||
    element === overlay ||
    element === label ||
    element === toolbar ||
    toolbar.contains(element) ||
    element === document.documentElement ||
    element === document.body
  ) {
    overlay.style.display = 'none';
    label.style.display = 'none';
    return;
  }

  const rect = element.getBoundingClientRect();
  overlay.style.display = 'block';
  overlay.style.left = `${Math.max(rect.left, 0)}px`;
  overlay.style.top = `${Math.max(rect.top, 0)}px`;
  overlay.style.width = `${Math.max(rect.width, 1)}px`;
  overlay.style.height = `${Math.max(rect.height, 1)}px`;

  const selector = buildSelector(element);
  label.textContent = selector;
  label.style.display = 'block';
  label.style.left = `${Math.max(rect.left, 8)}px`;
  label.style.top = `${Math.max(rect.top - 28, 8)}px`;
}

// ── Selector builder ──────────────────────────────────────────────────────
function buildSelector(element) {
  const id = element.getAttribute('id');
  if (id && !looksGenerated(id)) {
    return `#${escapeIdentifier(id)}`;
  }

  for (const attr of ['data-testid', 'data-test', 'data-cy', 'name', 'aria-label']) {
    const value = element.getAttribute(attr);
    if (value && !looksGenerated(value)) {
      return `${element.tagName.toLowerCase()}[${attr}="${escapeAttribute(value)}"]`;
    }
  }

  const path = [];
  let current = element;
  while (current instanceof Element && current !== document.body && current !== document.documentElement && path.length < 5) {
    path.unshift(buildSegment(current));
    const selector = path.join(' > ');
    try {
      if (document.querySelectorAll(selector).length === 1) {
        return selector;
      }
    } catch {
      // invalid selector, keep traversing up
    }
    current = current.parentElement;
  }
  return path.join(' > ') || element.tagName.toLowerCase();
}

function buildSegment(element) {
  const tag = element.tagName.toLowerCase();
  const classes = Array.from(element.classList).filter((className) => !looksGenerated(className)).slice(0, 2);
  if (classes.length > 0) {
    return `${tag}.${classes.map(escapeIdentifier).join('.')}`;
  }
  const parent = element.parentElement;
  if (parent === null) {
    return tag;
  }
  const siblings = Array.from(parent.children).filter((child) => child.tagName === element.tagName);
  if (siblings.length <= 1) {
    return tag;
  }
  return `${tag}:nth-of-type(${siblings.indexOf(element) + 1})`;
}

function looksGenerated(value) {
  return /(^|[-_])[a-f0-9]{6,}($|[-_])/i.test(value) || /^[a-z]{1,4}[-_][a-z0-9_-]{6,}$/i.test(value);
}

function escapeIdentifier(value) {
  if (window.CSS && typeof window.CSS.escape === 'function') {
    return window.CSS.escape(value);
  }
  return String(value).replace(/([^a-zA-Z0-9_-])/g, '\\$1');
}

function escapeAttribute(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function normalizeText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim().slice(0, 160);
}
