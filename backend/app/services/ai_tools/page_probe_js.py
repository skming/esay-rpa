"""inspect_page 注入页面的探测脚本。

只提取 HTML 客观事实、不做结构预解读，语义判断交给 AI；
"""
from __future__ import annotations

PAGE_PROBE_JS = """(scopeSelector) => {
    const MAX = 60;
    const root = scopeSelector
        ? (document.querySelector(scopeSelector) || document)
        : document;

    function text(el) {
        return (el.innerText || el.textContent || el.value || el.placeholder || '')
            .trim().replace(/\\s+/g, ' ').slice(0, 80);
    }

    // Stable CSS selector — prefers id/name/placeholder/type, falls back to :has-text()
    function selector(el) {
        if (el.id && !/^\\d/.test(el.id)) return '#' + CSS.escape(el.id);
        if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
        if (el.placeholder) return el.tagName.toLowerCase() + '[placeholder="' + el.placeholder + '"]';
        if (el.type && el.type !== 'text') return el.tagName.toLowerCase() + '[type="' + el.type + '"]';
        const t = text(el).slice(0, 30);
        if (t) return el.tagName.toLowerCase() + ':has-text("' + t + '")';
        return el.tagName.toLowerCase();
    }

    // Label for a form field: HTML semantics only, no class-name guessing.
    // 1. <label for="id">  2. enclosing <label>/<fieldset>  3. preceding sibling text
    function labelFor(el) {
        if (el.id) {
            const lbl = document.querySelector('label[for="' + el.id + '"]');
            if (lbl) return lbl.innerText.trim().slice(0, 40);
        }
        const wrap = el.closest('label, fieldset, [role=group]');
        if (wrap) {
            const t = (wrap.querySelector('legend')?.innerText
                || [...wrap.childNodes].filter(n => n.nodeType === 3)
                       .map(n => n.textContent.trim()).join(' ')).trim();
            if (t) return t.slice(0, 40);
        }
        const prev = el.previousElementSibling;
        if (prev) {
            const t = (prev.innerText || prev.textContent || '').trim();
            if (t && t.length < 50) return t.slice(0, 40);
        }
        return null;
    }

    // selector 完全相同的元素对模型没有区分度，却会占满 MAX 名额：
    // 表格里几十个同款 radio / 「编辑」按钮能把筛选区真正要操作的控件整个挤出列表。
    function dedupeBySelector(items) {
        const seen = new Set();
        return items.filter(item => {
            if (!item.selector || seen.has(item.selector)) return false;
            seen.add(item.selector);
            return true;
        });
    }

    // ── Form fields (standard HTML, always accurate) ─────────────────
    // 有 placeholder/name/id 的输入框排在无标识的前面：截断时先保住可定位的那些。
    const inputs = dedupeBySelector(
        [...root.querySelectorAll('input:not([type=hidden]), textarea')].map(el => ({
            tag: el.tagName.toLowerCase(),
            type: el.type || null,
            name: el.name || null,
            id: el.id || null,
            placeholder: el.placeholder || null,
            label: labelFor(el),
            // readonly 输入框通常是「点开弹层选择」的组件触发器而非可自由键入的文本框；
            // value 暴露该控件真实接受的文本格式（如 2026-06-01 / 2026/06/01），
            // 二者都与组件库无关，未知框架也能据此判断该怎么交互。
            readonly: el.readOnly || null,
            value: (el.value || '').slice(0, 40) || null,
            selector: selector(el),
        }))
    ).sort((a, b) => (a.placeholder || a.name || a.id ? 0 : 1) - (b.placeholder || b.name || b.id ? 0 : 1))
     .slice(0, MAX);

    const selects = [...root.querySelectorAll('select')].slice(0, 20).map(el => ({
        name: el.name || null,
        id: el.id || null,
        label: labelFor(el),
        selector: selector(el),
        options: [...el.options].map(o => o.text.trim()).filter(Boolean).slice(0, 20),
    }));

    // ── Buttons (standard HTML + ARIA) ───────────────────────────────
    const buttons = dedupeBySelector(
        [...root.querySelectorAll(
            'button, input[type=submit], input[type=button], [role=button]'
        )].map(el => ({ text: text(el), type: el.type || null, selector: selector(el) }))
          .filter(b => b.text)
    ).slice(0, MAX);

    // ── All visible links — AI interprets which are navigation/action/content ──
    const links = [...root.querySelectorAll('a[href]')]
        .filter(el => text(el).length > 0)
        .slice(0, MAX)
        .map(el => ({
            text: text(el),
            href: el.href || null,
            selector: selector(el),
            cls: String(el.className || '').slice(0, 60),
        }));

    // ── Business-scope helpers for table row selectors ───────────────
    // A "business class" is one that is NOT a framework prefix AND NOT a
    // generic layout word — it uniquely identifies a specific table on the page.
    const FRAMEWORK_RE = /^(el|ant|arco|vxe|n|van|ivu|layui|semi|tdesign|varlet|vc|v)-/;
    const LAYOUT_WORDS = new Set([
        'app','page','main','layout','content','wrapper','container',
        'inner','outer','shell','frame','view','root','section',
        'area','panel','box','wrap','base','center','body','fluid','fixed','scroll',
    ]);
    function isLayoutOnly(cls) {
        const words = cls.toLowerCase().split(/[-_]/).filter(Boolean);
        return words.length > 0 && words.every(w => LAYOUT_WORDS.has(w));
    }
    function isBusinessClass(cls) {
        return cls.length > 2 && !FRAMEWORK_RE.test(cls) && !isLayoutOnly(cls);
    }
    // Walk up from el to find the nearest ancestor with a business-domain class.
    function nearestBizAncestor(el) {
        let cur = el.parentElement;
        while (cur && cur !== document.body) {
            const classes = String(cur.className || '').split(/\\s+/).filter(Boolean);
            const bizCls = classes.find(isBusinessClass);
            if (bizCls) return { el: cur, cls: bizCls };
            cur = cur.parentElement;
        }
        return null;
    }
    // Build a business-scoped row selector for a table element.
    function bizRowSelector(tbl) {
        const anc = nearestBizAncestor(tbl);
        if (!anc) return null;
        const scope = '.' + CSS.escape(anc.cls);
        // Prefer <tbody tr> (standard HTML), then look for a framework row class.
        if (tbl.querySelector('tbody tr')) return scope + ' tr';
        const rowEl = tbl.querySelector('[class*="row"], [class*="__row"], [class*="-row"]');
        if (rowEl) {
            const rowCls = [...rowEl.classList].find(c =>
                /row|__row|-row|--row/.test(c) && !isLayoutOnly(c)
            );
            if (rowCls) return scope + ' .' + CSS.escape(rowCls);
        }
        return scope + ' tr';
    }

    // ── Tables: standard HTML + ARIA grid/table ──────────────────────
    // Also catches custom components that render <th>/<role=columnheader> rows.
    const tableElSet = new Set([...root.querySelectorAll('table, [role=grid], [role=table]')]);
    [...root.querySelectorAll('[class]:not(table)')].forEach(el => {
        if (el.querySelector('th, [role=columnheader]')) tableElSet.add(el);
    });
    const tables = [...tableElSet].slice(0, 5).map(tbl => {
        const headers = [...tbl.querySelectorAll('th, [role=columnheader]')]
            .map(th => text(th)).filter(Boolean);
        return {
            headers,
            // Named container_selector, not selector: browser.extract needs the row path,
            // and a field called "selector" gets copied into it verbatim.
            container_selector: selector(tbl),
            cls: String(tbl.className || '').slice(0, 60),
            // row_selector: business-scoped path to data rows — use this for browser.extract extractMode=table
            row_selector: bizRowSelector(tbl),
        };
    });

    // ── Currently-visible picker options (ARIA only) ─────────────────
    // Only populated when a dropdown/listbox is actually open.
    const visibleOptions = [...document.querySelectorAll('[role=option], [aria-selected]')]
        .slice(0, 40).map(el => text(el)).filter(Boolean);

    // ── All CSS class names on the page ──────────────────────────────
    // AI uses this to identify the UI framework (el-/ant-/arco-/n-/custom).
    const classSet = new Set();
    document.querySelectorAll('[class]').forEach(el =>
        String(el.className).split(/\\s+/).forEach(c => {
            if (c.length > 2 && c.length < 40) classSet.add(c);
        })
    );
    // 组件库指纹常常出现在 DOM 靠后的位置（筛选区、下拉浮层），按文档序截断会把它们整段丢掉，
    // 组件识别就永远匹配不上。所以：截断只用于给模型看，识别一律用全量集合。
    const allCls = [...classSet];
    const pageCls = allCls.slice(0, 120);

    // ── Page layout: top-level structural elements + their HTML ───────
    // Dynamic — no fixed categories. Body direct children; goes one level
    // deeper when the SPA shell wraps everything in 1-2 divs.
    const SKIP = new Set(['script','style','noscript','link','meta','title']);
    function meaningfulChildren(parent, limit) {
        return [...parent.children]
            .filter(el => !SKIP.has(el.tagName.toLowerCase()) && el.textContent.trim().length > 5)
            .slice(0, limit);
    }
    const bodyKids = meaningfulChildren(document.body, 10);
    const structuralEls = bodyKids.length <= 2
        ? bodyKids.flatMap(el => meaningfulChildren(el, 5)).slice(0, 8)
        : bodyKids.slice(0, 6);
    const pageLayout = structuralEls.map(el => ({
        tag: el.tagName.toLowerCase(),
        cls: String(el.className || '').slice(0, 80),
        role: el.getAttribute('role') || null,
        id: el.id || null,
        aria_label: el.getAttribute('aria-label') || null,
        html: el.outerHTML.replace(/<script[\\s\\S]*?<\\/script>/gi, '').slice(0, 2000),
    }));

    return {
        url: window.location.href,
        title: document.title,
        inputs,
        selects,
        buttons,
        links,
        tables,
        visible_options: visibleOptions,
        page_classes: pageCls,
        all_classes: allCls,   // 仅供服务端做组件识别/加载态判断，返回给模型前会被移除
        page_layout: pageLayout,
    };
}"""
