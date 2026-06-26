"""Headed-browser element picker service.

Opens the target URL inside the same persistent Playwright BrowserContext that
the RPA engine uses, so the picker shares ALL browser state: cookies,
localStorage, sessionStorage, IndexedDB, service workers — everything.

Result is delivered via an asyncio.Queue that callers can await.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


# JavaScript injected before any page script via add_init_script.
# Uses window.__rpaPickerCapture__ / __rpaPickerCancel__ which are exposed
# by Playwright's page.expose_function before navigation starts.
_PICKER_OVERLAY_JS = r"""
(function () {
  /* ═══════════════════════════════════════════════════════════════════════
     State
  ═══════════════════════════════════════════════════════════════════════ */
  let pickingEnabled = true;
  let currentEl = null;

  /* ═══════════════════════════════════════════════════════════════════════
     Styles injected into <head>
  ═══════════════════════════════════════════════════════════════════════ */
  const styleEl = document.createElement('style');
  styleEl.textContent = `
    .__rpa_picking__ * { cursor: crosshair !important; }

    /* ── Edge breathing glow — thin strips, concentrated at edge ────── */
    .__rpa_gl__ {
      position: fixed;
      z-index: 2147483641;
      pointer-events: none;
      animation: __rpa_breathe__ 2.2s ease-in-out infinite;
    }
    /* 16px wide, color fades to transparent within that 16px */
    .__rpa_gl_t__ { top: 44px; left: 0; right: 0; height: 16px;
      background: linear-gradient(to bottom, rgba(95,82,238,0.95) 0%, rgba(99,102,241,0.3) 55%, transparent 100%); }
    .__rpa_gl_b__ { bottom: 0; left: 0; right: 0; height: 16px;
      background: linear-gradient(to top,    rgba(95,82,238,0.95) 0%, rgba(99,102,241,0.3) 55%, transparent 100%); }
    .__rpa_gl_l__ { top: 44px; bottom: 0; left: 0; width: 16px;
      background: linear-gradient(to right,  rgba(95,82,238,0.95) 0%, rgba(99,102,241,0.3) 55%, transparent 100%); }
    .__rpa_gl_r__ { top: 44px; bottom: 0; right: 0; width: 16px;
      background: linear-gradient(to left,   rgba(95,82,238,0.95) 0%, rgba(99,102,241,0.3) 55%, transparent 100%); }
    @keyframes __rpa_breathe__ {
      0%,100% { opacity: 0.2; }
      50%      { opacity: 1;   }
    }
    .__rpa_gl__.paused { animation-play-state: paused; opacity: 0.06 !important; }

    /* ── Highlight overlay ───────────────────────────────────────────── */
    .__rpa_highlight__ {
      position: fixed;
      z-index: 2147483644;
      pointer-events: none;
      border: 2px solid #818cf8;
      border-radius: 4px;
      background: rgba(95,82,238,0.08);
      transition: top .07s,left .07s,width .07s,height .07s;
      box-shadow: 0 0 0 3px rgba(95,82,238,0.2),
                  0 0 12px rgba(95,82,238,0.4);
    }
    .__rpa_dims__ {
      position: fixed;
      z-index: 2147483645;
      pointer-events: none;
      font: 10px ui-monospace,SFMono-Regular,Menlo,monospace;
      color: #c7d2fe;
      background: rgba(8,10,28,0.9);
      padding: 2px 7px;
      border-radius: 4px;
      border: 1px solid rgba(95,82,238,0.35);
    }

    /* ── Tooltip ─────────────────────────────────────────────────────── */
    .__rpa_tooltip__ {
      position: fixed;
      z-index: 2147483646;
      pointer-events: none;
      background: rgba(8,10,28,0.97);
      border: 1px solid rgba(95,82,238,0.3);
      border-radius: 10px;
      padding: 10px 13px;
      min-width: 230px;
      max-width: 500px;
      box-shadow: 0 16px 40px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.04),
                  0 0 20px rgba(95,82,238,0.1);
      backdrop-filter: blur(8px);
    }
    .__rpa_tooltip__ .__tag__ {
      font: 700 11px ui-monospace,SFMono-Regular,Menlo,monospace;
      color: #a5b4fc;
      margin-bottom: 5px;
      letter-spacing: .04em;
    }
    .__rpa_tooltip__ .__sel__ {
      font: 11px ui-monospace,SFMono-Regular,Menlo,monospace;
      color: #e2e8f0;
      word-break: break-all;
      line-height: 1.6;
    }
    .__rpa_tooltip__ .__hint__ {
      margin-top: 8px;
      padding-top: 7px;
      border-top: 1px solid rgba(255,255,255,0.06);
      font: 10px ui-sans-serif,system-ui,sans-serif;
      color: #64748b;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .__rpa_tooltip__ .__hint__::before {
      content: '';
      display: inline-block;
      width: 5px; height: 5px;
      border-radius: 50%;
      background: #818cf8;
      animation: __rpa_pulse__ 1.4s infinite;
      flex-shrink: 0;
    }

    /* ── Toolbar ─────────────────────────────────────────────────────── */
    .__rpa_toolbar__ {
      position: fixed;
      top: 0; left: 0; right: 0;
      z-index: 2147483647;
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 0 12px;
      height: 44px;
      background: rgba(14, 18, 30, 0.96);
      border-bottom: 1px solid rgba(255,255,255,0.07);
      box-shadow: 0 1px 0 rgba(255,255,255,0.04), 0 4px 24px rgba(0,0,0,0.7);
      backdrop-filter: blur(24px) saturate(180%);
      -webkit-backdrop-filter: blur(24px) saturate(180%);
      font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",ui-sans-serif,sans-serif;
      font-size: 13px;
      color: #e2e8f0;
      user-select: none;
    }

    /* ── Logo ────────────────────────────────────────────────────────── */
    .__rpa_logo__ {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      flex-shrink: 0;
      padding-right: 10px;
      margin-right: 2px;
      border-right: 1px solid rgba(255,255,255,0.09);
    }
    .__rpa_logo__ span {
      font-size: 13px;
      font-weight: 600;
      color: #f8fafc;
      letter-spacing: -.01em;
    }

    /* ── Badge ───────────────────────────────────────────────────────── */
    .__rpa_badge__ {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 3px 9px 3px 7px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 500;
      flex-shrink: 0;
      transition: all .25s;
    }
    .__rpa_badge__.active {
      background: rgba(95,82,238,0.15);
      color: #a5b4fc;
      border: 1px solid rgba(95,82,238,0.3);
    }
    .__rpa_badge__.paused {
      background: rgba(255,255,255,0.05);
      color: #64748b;
      border: 1px solid rgba(255,255,255,0.07);
    }
    .__rpa_badge__ .__dot__ {
      width: 6px; height: 6px;
      border-radius: 50%;
      background: currentColor;
      animation: __rpa_pulse__ 1.6s ease-in-out infinite;
    }
    .__rpa_badge__.paused .__dot__ { animation: none; opacity: .3; }
    @keyframes __rpa_pulse__ {
      0%,100% { opacity: 1; transform: scale(1); }
      50%      { opacity: .35; transform: scale(0.75); }
    }

    /* ── Divider ─────────────────────────────────────────────────────── */
    .__rpa_sep__ {
      width: 1px; height: 20px;
      background: rgba(255,255,255,0.08);
      flex-shrink: 0;
      margin: 0 2px;
    }

    /* ── Nav buttons ─────────────────────────────────────────────────── */
    .__rpa_nav__ {
      display: flex;
      align-items: center;
      gap: 0;
      flex-shrink: 0;
    }
    .__rpa_icon_btn__ {
      width: 30px; height: 30px;
      border: none;
      border-radius: 6px;
      background: transparent;
      color: #64748b;
      cursor: pointer;
      display: grid;
      place-items: center;
      transition: background .1s, color .1s;
      font-size: 14px;
    }
    .__rpa_icon_btn__:hover { background: rgba(255,255,255,0.07); color: #cbd5e1; }
    .__rpa_icon_btn__:disabled { opacity: .2; cursor: default; }

    /* ── URL bar ─────────────────────────────────────────────────────── */
    .__rpa_url_wrap__ {
      flex: 1;
      min-width: 0;
      display: flex;
      align-items: center;
      background: rgba(255,255,255,0.06);
      border: 1px solid rgba(255,255,255,0.1);
      border-radius: 8px;
      padding: 0 6px 0 8px;
      gap: 5px;
      height: 30px;
      transition: border-color .15s, background .15s;
    }
    .__rpa_url_wrap__:focus-within {
      border-color: rgba(95,82,238,0.5);
      background: rgba(95,82,238,0.04);
    }
    .__rpa_url_lock__ {
      flex-shrink: 0;
      opacity: 0.35;
      display: flex;
      align-items: center;
    }
    .__rpa_url_input__ {
      flex: 1;
      min-width: 0;
      background: transparent;
      border: none;
      outline: none;
      color: #94a3b8;
      font: 12px ui-monospace,SFMono-Regular,Menlo,monospace;
      caret-color: #818cf8;
    }
    .__rpa_url_input__::placeholder { color: #334155; }

    /* ── Action buttons ──────────────────────────────────────────────── */
    .__rpa_btn__ {
      height: 30px;
      padding: 0 14px;
      border-radius: 7px;
      border: 1px solid transparent;
      font: 500 12px -apple-system,BlinkMacSystemFont,"Segoe UI",ui-sans-serif,sans-serif;
      cursor: pointer;
      white-space: nowrap;
      transition: all .12s;
      flex-shrink: 0;
    }
    /* 继续/暂停拾取 — solid brand when paused, ghost when active */
    .__rpa_btn__.toggle-pick {
      background: rgba(255,255,255,0.06);
      border-color: rgba(255,255,255,0.1);
      color: #94a3b8;
    }
    .__rpa_btn__.toggle-pick:hover {
      background: rgba(255,255,255,0.1);
      color: #e2e8f0;
    }
    .__rpa_btn__.toggle-pick.paused {
      background: #5f52ee;
      border-color: transparent;
      color: #fff;
      box-shadow: 0 1px 10px rgba(95,82,238,0.55);
    }
    .__rpa_btn__.toggle-pick.paused:hover {
      background: #4e40db;
      box-shadow: 0 2px 14px rgba(95,82,238,0.65);
    }
    /* 关闭 */
    .__rpa_btn__.close-picker {
      background: rgba(239,68,68,0.1);
      border-color: rgba(239,68,68,0.25);
      color: #f87171;
    }
    .__rpa_btn__.close-picker:hover {
      background: rgba(239,68,68,0.2);
      border-color: rgba(239,68,68,0.4);
      color: #fca5a5;
    }
  `;

  /* ═══════════════════════════════════════════════════════════════════════
     DOM elements
  ═══════════════════════════════════════════════════════════════════════ */
  function _glowStrip(side) {
    const d = document.createElement('div');
    d.className = `__rpa_gl__ __rpa_gl_${side}__`;
    return d;
  }
  const glowT = _glowStrip('t'), glowB = _glowStrip('b'),
        glowL = _glowStrip('l'), glowR = _glowStrip('r');
  const allGlows = [glowT, glowB, glowL, glowR];

  const highlight = document.createElement('div');
  highlight.className = '__rpa_highlight__';

  const dimsLabel = document.createElement('div');
  dimsLabel.className = '__rpa_dims__';

  const tooltip = document.createElement('div');
  tooltip.className = '__rpa_tooltip__';
  tooltip.innerHTML = `
    <div class="__tag__"></div>
    <div class="__sel__"></div>
    <div class="__hint__">点击捕获元素 · Esc 关闭</div>
  `;

  const toolbar = document.createElement('div');
  toolbar.className = '__rpa_toolbar__';

  /* logo */
  const logo = document.createElement('div');
  logo.className = '__rpa_logo__';
  logo.innerHTML = `<svg width="18" height="18" viewBox="0 0 18 18" fill="none">
    <rect x="1.5" y="1.5" width="15" height="15" rx="3.5" stroke="#818cf8" stroke-width="1.6"/>
    <circle cx="9" cy="9" r="2.8" fill="#818cf8" fill-opacity="0.9"/>
    <path d="M9 3.5v2.5M9 12v2.5M3.5 9H6M12 9h2.5" stroke="#818cf8" stroke-width="1.4" stroke-linecap="round"/>
  </svg><span>RPA 拾取器</span>`;

  /* badge */
  const badge = document.createElement('span');
  badge.className = '__rpa_badge__ active';
  badge.innerHTML = '<span class="__dot__"></span><span>拾取中</span>';

  /* nav */
  const nav = document.createElement('div');
  nav.className = '__rpa_nav__';
  const backBtn   = _iconBtn('&#8592;', '后退');
  const fwdBtn    = _iconBtn('&#8594;', '前进');
  const reloadBtn = _iconBtn('&#8635;', '刷新');
  nav.append(backBtn, fwdBtn, reloadBtn);

  /* url bar */
  const urlWrap = document.createElement('div');
  urlWrap.className = '__rpa_url_wrap__';

  const lockIcon = document.createElement('span');
  lockIcon.className = '__rpa_url_lock__';
  lockIcon.innerHTML = `<svg width="11" height="13" viewBox="0 0 11 13" fill="none">
    <rect x="1" y="5.5" width="9" height="7" rx="1.5" stroke="#a5b4fc" stroke-width="1.2"/>
    <path d="M3 5.5V3.5a2.5 2.5 0 015 0v2" stroke="#a5b4fc" stroke-width="1.2" stroke-linecap="round"/>
  </svg>`;

  const urlInput = document.createElement('input');
  urlInput.className = '__rpa_url_input__';
  urlInput.placeholder = 'https://...';
  const goBtn = _iconBtn('&#8617;', '跳转 Enter');
  urlWrap.append(lockIcon, urlInput, goBtn);

  /* separators */
  const sep1 = document.createElement('div');  /* nav | url */
  sep1.className = '__rpa_sep__';
  const sep2 = document.createElement('div');  /* url | actions */
  sep2.className = '__rpa_sep__';

  const toggleBtn = document.createElement('button');
  toggleBtn.className = '__rpa_btn__ toggle-pick';
  toggleBtn.textContent = '暂停拾取';

  const closeBtn = document.createElement('button');
  closeBtn.className = '__rpa_btn__ close-picker';
  closeBtn.textContent = '关闭';

  toolbar.append(logo, badge, sep1, nav, urlWrap, sep2, toggleBtn, closeBtn);

  /* ═══════════════════════════════════════════════════════════════════════
     State helpers
  ═══════════════════════════════════════════════════════════════════════ */
  function setPickingEnabled(on) {
    pickingEnabled = on;
    if (on) {
      badge.className = '__rpa_badge__ active';
      badge.innerHTML = '<span class="__dot__"></span><span>拾取中</span>';
      toggleBtn.className = '__rpa_btn__ toggle-pick';
      toggleBtn.textContent = '暂停拾取';
      document.documentElement.classList.add('__rpa_picking__');
      allGlows.forEach(g => g.classList.remove('paused'));
    } else {
      badge.className = '__rpa_badge__ paused';
      badge.innerHTML = '<span class="__dot__"></span><span>已暂停</span>';
      toggleBtn.className = '__rpa_btn__ toggle-pick paused';
      toggleBtn.textContent = '继续拾取';
      document.documentElement.classList.remove('__rpa_picking__');
      allGlows.forEach(g => g.classList.add('paused'));
      _hide();
    }
  }

  function _hide() {
    highlight.style.display = 'none';
    dimsLabel.style.display = 'none';
    tooltip.style.display = 'none';
    currentEl = null;
  }

  function _updateHighlight(el) {
    const r = el.getBoundingClientRect();
    const w = Math.round(r.width), h = Math.round(r.height);
    const t = Math.max(r.top, 44), l = Math.max(r.left, 0);

    highlight.style.cssText = `display:block;top:${t}px;left:${l}px;width:${Math.max(w,1)}px;height:${Math.max(h,1)}px`;

    /* dims label — top-right of element */
    dimsLabel.textContent = `${w} × ${h}`;
    dimsLabel.style.cssText = `display:block;top:${Math.max(t-18,48)}px;left:${Math.min(l+w-60, window.innerWidth-70)}px`;

    /* tooltip — prefer below element, fallback above */
    const sel = _buildSelector(el);
    tooltip.querySelector('.__tag__').textContent = `<${el.tagName.toLowerCase()}>`;
    tooltip.querySelector('.__sel__').textContent = sel;
    const ttH = 90;
    const ttTop = r.bottom + 8 + ttH < window.innerHeight ? r.bottom + 8 : Math.max(t - ttH - 8, 48);
    tooltip.style.cssText = `display:block;top:${ttTop}px;left:${Math.min(Math.max(l,8), window.innerWidth-300)}px`;
  }

  /* ═══════════════════════════════════════════════════════════════════════
     Event handlers
  ═══════════════════════════════════════════════════════════════════════ */
  toggleBtn.addEventListener('click', e => { e.stopPropagation(); setPickingEnabled(!pickingEnabled); });
  closeBtn.addEventListener('click',  e => { e.stopPropagation(); window.__rpaPickerCancel__(); });

  backBtn.addEventListener('click',   e => { e.stopPropagation(); history.back(); });
  fwdBtn.addEventListener('click',    e => { e.stopPropagation(); history.forward(); });
  reloadBtn.addEventListener('click', e => { e.stopPropagation(); location.reload(); });

  goBtn.addEventListener('click', e => {
    e.stopPropagation();
    const u = urlInput.value.trim();
    if (u) location.href = u.startsWith('http') ? u : 'https://' + u;
  });
  urlInput.addEventListener('keydown', e => {
    e.stopPropagation();
    if (e.key === 'Enter') goBtn.click();
  });
  urlInput.addEventListener('focus', () => urlInput.select());
  urlInput.addEventListener('click', e => e.stopPropagation());
  toolbar.addEventListener('mousedown', e => e.stopPropagation());

  window.addEventListener('mousemove', e => {
    if (!pickingEnabled) return;
    const el = e.target instanceof Element ? e.target : null;
    if (!el || toolbar.contains(el)) { _hide(); return; }
    if (el === currentEl) return;
    currentEl = el;
    _updateHighlight(el);
  }, true);

  window.addEventListener('click', e => {
    if (!pickingEnabled) return;
    const el = e.target instanceof Element ? e.target : null;
    if (!el || toolbar.contains(el)) return;
    e.preventDefault(); e.stopPropagation();
    const sel = _buildSelector(el);
    window.__rpaPickerCapture__({
      selector: sel,
      confidence: sel.startsWith('#') ? 0.96 : sel.includes('[data-') ? 0.92 : 0.78,
      text: String(el.textContent || '').replace(/\s+/g,' ').trim().slice(0,160),
      url: location.href
    });
  }, true);

  window.addEventListener('keydown', e => {
    if (e.key === 'Escape') window.__rpaPickerCancel__();
  });

  /* ═══════════════════════════════════════════════════════════════════════
     Init
  ═══════════════════════════════════════════════════════════════════════ */
  document.addEventListener('DOMContentLoaded', () => {
    document.head.appendChild(styleEl);
    document.documentElement.append(toolbar, ...allGlows, highlight, dimsLabel, tooltip);
    document.documentElement.style.marginTop = '44px';
    document.documentElement.classList.add('__rpa_picking__');
    urlInput.value = location.href;
  });
  window.addEventListener('load', () => { urlInput.value = location.href; });

  /* ═══════════════════════════════════════════════════════════════════════
     Selector builder
  ═══════════════════════════════════════════════════════════════════════ */
  function _buildSelector(el) {
    const id = el.getAttribute('id');
    if (id && !_looksGenerated(id)) return '#' + CSS.escape(id);
    for (const attr of ['data-testid','data-test','data-cy','name','aria-label']) {
      const v = el.getAttribute(attr);
      if (v && !_looksGenerated(v)) return `${el.tagName.toLowerCase()}[${attr}="${v.replace(/"/g,'\\"')}"]`;
    }
    const path = []; let cur = el;
    while (cur instanceof Element && cur !== document.body && path.length < 5) {
      path.unshift(_seg(cur));
      const sel = path.join(' > ');
      try { if (document.querySelectorAll(sel).length === 1) return sel; } catch {}
      cur = cur.parentElement;
    }
    return path.join(' > ') || el.tagName.toLowerCase();
  }
  function _seg(el) {
    const tag = el.tagName.toLowerCase();
    const cls = [...el.classList].filter(c => !_looksGenerated(c)).slice(0, 2);
    if (cls.length) return `${tag}.${cls.map(c => CSS.escape(c)).join('.')}`;
    const par = el.parentElement;
    if (!par) return tag;
    const sibs = [...par.children].filter(c => c.tagName === el.tagName);
    return sibs.length <= 1 ? tag : `${tag}:nth-of-type(${sibs.indexOf(el) + 1})`;
  }
  function _looksGenerated(v) {
    return /(^|[-_])[a-f0-9]{6,}($|[-_])/i.test(v) || /^[a-z]{1,4}[-_][a-z0-9_-]{6,}$/i.test(v);
  }
  function _iconBtn(html, title) {
    const b = document.createElement('button');
    b.className = '__rpa_icon_btn__';
    b.innerHTML = html;
    b.title = title;
    return b;
  }
})();
"""


class PickerService:
    def __init__(self, session_dir: str) -> None:
        self._session_dir = session_dir
        self._result_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._context: Any = None
        self._page: Any = None
        self._playwright: Any = None
        self._active = False

    async def open(self, target_url: str) -> None:
        if self._active:
            if self._page and not self._page.is_closed():
                await self._page.bring_to_front()
                return
            await self._cleanup()

        # Reset queue so stale None/results from a previous session don't leak
        self._result_queue = asyncio.Queue()

        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()
        profile_path = Path(self._session_dir)
        profile_path.mkdir(parents=True, exist_ok=True)

        self._context = await self._playwright.chromium.launch_persistent_context(
            str(profile_path),
            headless=False,
            args=["--disable-cache"],
        )
        self._page = await self._context.new_page()
        self._active = True

        await self._page.expose_function("__rpaPickerCapture__", self._on_capture)
        await self._page.expose_function("__rpaPickerCancel__", self._on_cancel)
        await self._page.add_init_script(_PICKER_OVERLAY_JS)

        self._page.on("close", lambda: asyncio.ensure_future(self._on_page_close()))
        await self._page.goto(target_url)

    async def close(self) -> None:
        if self._active:
            self._result_queue.put_nowait(None)
        await self._cleanup()

    async def wait_for_result(self) -> dict[str, Any] | None:
        return await self._result_queue.get()

    async def _on_capture(self, result: dict[str, Any]) -> None:
        # Put result first, then schedule cleanup OUTSIDE this callback so
        # Playwright finishes the bridge call before the context is closed.
        self._result_queue.put_nowait(result)
        asyncio.ensure_future(self._cleanup())

    async def _on_cancel(self) -> None:
        self._result_queue.put_nowait(None)
        asyncio.ensure_future(self._cleanup())

    async def _on_page_close(self) -> None:
        # Browser window closed by user without picking — unblock wait_for_result()
        if self._active:
            self._result_queue.put_nowait(None)
        await self._cleanup()

    async def _cleanup(self) -> None:
        self._active = False
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._page = None
        self._playwright = None
