/**
 * 页面观察探测脚本：Playwright 与 Chrome 扩展两条通道唯一的来源。
 *
 * 放在后端包里、由 page_probe_js.py 读成字符串，同时被扩展内容脚本 import。
 * 两边各写一份的代价不是重复，是漂移：扩展看到的「页面事实」和 Playwright 看到的
 * 一旦不同，同一页在两条通道上会得出不同的配方，而两边都自称观察过真实 DOM。
 *
 * 因此这个文件只许写两边都能跑的纯 DOM 代码：
 * - 不 import 任何东西（Playwright 侧是 evaluate 一个表达式，没有模块系统）；
 * - 除下面这一行 export 外不出现任何模块语法（Python 侧按这行切开取表达式）；
 * - 不用 TypeScript 语法（同上）；
 * - 只用内容脚本隔离世界里也成立的 API：document/getComputedStyle/CSS.escape 都是共享的，
 *   而 window 不是——window.__rpaProbe 在扩展侧落在隔离世界，页面脚本改不到它，正好。
 */
export const PAGE_PROBE = (args) => {
    const scopeSelector = (args && args.scope) || null;
    const version = (args && args.version) || 0;
    const includeHtml = !!(args && args.includeHtml);
    const MAX = 60;

    // ── 一次观察 = 一个 ref 注册表 ────────────────────────────────
    // 整表替换而不是追加：上一次观察的 ref 编号在新版本里必须失效，
    // 否则模型会拿旧编号操作重渲染后的元素，点到的是同位置的另一行数据。
    const reg = { version: version, els: [] };
    window.__rpaProbe = reg;
    function ref(el) { reg.els.push(el); return 'e' + (reg.els.length - 1); }

    function text(el) {
        return (el.innerText || el.textContent || el.value || el.placeholder || '')
            .trim().replace(/\s+/g, ' ').slice(0, 80);
    }

    // 只认渲染文本，不碰 value：text() 为了读出 <input type=submit> 的按钮文字会回落到
    // el.value，任意选择器都能被指到表单控件上，那一落就是用户输入的明文（含 password）。
    // 脱敏边界与 inputs[].value 同一条——凭据不出页面，所以这里对表单控件直接不给文本。
    function renderedText(el) {
        if (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return '';
        return (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80);
    }

    // ── 开放 shadow root 穿透 ─────────────────────────────────────
    // 闭合 shadow root 在页面脚本里和「没有 shadow root」完全无法区分，
    // 所以只声明开放的那部分，另外把可疑的自定义元素单独报出去。
    function collectRoots(node, out) {
        out.push(node);
        let all = [];
        try { all = node.querySelectorAll('*'); } catch (e) { all = []; }
        for (const el of all) if (el.shadowRoot) collectRoots(el.shadowRoot, out);
        return out;
    }
    function queryAll(roots, sel) {
        const out = [];
        for (const r of roots) {
            try { r.querySelectorAll(sel).forEach(e => out.push(e)); } catch (e) { /* 非法选择器 */ }
        }
        return out;
    }

    const DOC_ROOTS = collectRoots(document, []);
    const inShadow = new WeakSet();
    for (const r of DOC_ROOTS) {
        if (r.host) { try { r.querySelectorAll('*').forEach(e => inShadow.add(e)); } catch (e) {} }
    }

    // ── 选择器：先求唯一，求不到就如实报命中数 ──────────────────
    const countCache = new Map();
    function countDoc(sel) {
        if (countCache.has(sel)) return countCache.get(sel);
        let n = -1;   // -1 = 该选择器在浏览器里非法，命中数不可知
        try { n = queryAll(DOC_ROOTS, sel).length; } catch (e) { n = -1; }
        countCache.set(sel, n);
        return n;
    }

    // 属性值里带引号或反斜杠时不构造属性选择器：转义规则在两个执行器上不完全一致，
    // 拼错的选择器会静默零命中，不如直接退到结构路径。
    function attrSel(tag, name, value) {
        if (!value || /["\\]/.test(value) || value.length > 60) return null;
        return tag + '[' + name + '="' + value + '"]';
    }

    function candidates(el) {
        const tag = el.tagName.toLowerCase();
        const out = [];
        if (el.id && !/^\d/.test(el.id)) out.push('#' + CSS.escape(el.id));
        const attrs = [['name', el.getAttribute('name')], ['placeholder', el.getAttribute('placeholder')], ['title', el.getAttribute('title')],
                       ['aria-label', el.getAttribute('aria-label')], ['data-testid', el.getAttribute('data-testid')]];
        for (const [k, v] of attrs) { const s = attrSel(tag, k, v); if (s) out.push(s); }
        if (el.type && el.type !== 'text') { const s = attrSel(tag, 'type', el.type); if (s) out.push(s); }
        return out;
    }

    // 结构路径：一定合法、一定唯一，两个执行器都能解析。停在最近的唯一 id 祖先。
    function cssPath(el) {
        const parts = [];
        let cur = el;
        while (cur && cur.nodeType === 1 && parts.length < 8) {
            if (cur.id && !/^\d/.test(cur.id) && countDoc('#' + CSS.escape(cur.id)) === 1) {
                parts.unshift('#' + CSS.escape(cur.id));
                return parts.join(' > ');
            }
            const parent = cur.parentElement;
            const tag = cur.tagName.toLowerCase();
            if (!parent) { parts.unshift(tag); break; }
            const sibs = [...parent.children].filter(c => c.tagName === cur.tagName);
            parts.unshift(sibs.length > 1 ? tag + ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')' : tag);
            cur = parent;
        }
        return parts.join(' > ');
    }

    // ── 业务类名 / 框架类名 ───────────────────────────────────────
    // 业务类名 = 既不是组件库前缀、也不是通用布局词，能唯一指认页面上某个具体区域。
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
    function classesOf(el) {
        return String(el.className || '').split(/\s+/).filter(Boolean);
    }
    function parentOf(el) {
        if (el.parentElement) return el.parentElement;
        const r = el.getRootNode();
        return (r && r.host) ? r.host : null;
    }

    let uidSeq = 0;
    const uidMap = new WeakMap();
    function uid(el) {
        let u = uidMap.get(el);
        if (u === undefined) { u = ++uidSeq; uidMap.set(el, u); }
        return u;
    }

    // 同一个祖先会被几十个输入框反复走到，选择器计算必须缓存，否则整页探测退化成上千次全文档匹配。
    const selCache = new WeakMap();
    function bestSelector(el) {
        const hit = selCache.get(el);
        if (hit) return hit;
        let out = null;
        const cands = candidates(el);
        for (const c of cands) if (countDoc(c) === 1) { out = { selector: c, matches: 1 }; break; }
        if (!out) {
            // 裸选择器不唯一时先用祖先限定，比 nth-of-type 路径可读，也不随兄弟节点增减而失效
            for (const c of cands) {
                let cur = parentOf(el), depth = 0;
                while (cur && cur.nodeType === 1 && depth++ < 6) {
                    const anchors = [];
                    if (cur.id && !/^\d/.test(cur.id)) anchors.push('#' + CSS.escape(cur.id));
                    for (const cls of classesOf(cur)) if (isBusinessClass(cls)) anchors.push('.' + CSS.escape(cls));
                    for (const a of anchors) {
                        const scoped = a + ' ' + c;
                        if (countDoc(scoped) === 1) { out = { selector: scoped, matches: 1 }; break; }
                    }
                    if (out) break;
                    cur = parentOf(cur);
                }
                if (out) break;
            }
        }
        if (!out) { const p = cssPath(el); out = { selector: p, matches: countDoc(p) }; }
        selCache.set(el, out);
        return out;
    }

    // ── 语义与状态 ────────────────────────────────────────────────
    const IMPLICIT_ROLE = { button: 'button', a: 'link', select: 'combobox', textarea: 'textbox', table: 'table' };
    const INPUT_ROLE = { checkbox: 'checkbox', radio: 'radio', submit: 'button', button: 'button',
                         reset: 'button', range: 'slider', number: 'spinbutton', search: 'searchbox' };
    function implicitRole(el) {
        const tag = el.tagName.toLowerCase();
        if (tag === 'input') return INPUT_ROLE[(el.type || 'text').toLowerCase()] || 'textbox';
        if (tag === 'a') return el.getAttribute('href') ? 'link' : null;
        return IMPLICIT_ROLE[tag] || null;
    }
    // 只回报与标签默认角色不同的显式 role：`<button>` 上的 role=button 对模型没有信息量，
    // 而 `<div role=button>` 决定了它只能用 click、不能用 fill。
    function roleOf(el) {
        const explicit = el.getAttribute('role');
        if (!explicit) return null;
        return explicit === implicitRole(el) ? null : explicit;
    }
    function accName(el) {
        const direct = el.getAttribute('aria-label');
        if (direct && direct.trim()) return direct.trim().slice(0, 40);
        const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
        if (ids.length) {
            const t = ids.map(i => { const n = document.getElementById(i); return n ? (n.innerText || '').trim() : ''; })
                         .filter(Boolean).join(' ').trim();
            if (t) return t.slice(0, 40);
        }
        return null;
    }
    function isVisible(el) {
        if (!el.isConnected) return false;
        const cs = getComputedStyle(el);
        if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }
    function isDisabled(el) {
        return el.disabled === true || el.getAttribute('aria-disabled') === 'true';
    }

    // 字段缺省即默认值：visible=true / disabled=false / matches=1。
    // 60 个元素每个都带三个默认字段，光默认值就能占掉上千 token。
    function base(el) {
        const out = { ref: ref(el) };
        const bs = bestSelector(el);
        out.selector = bs.selector;
        if (bs.matches !== 1) out.matches = bs.matches;
        const r = roleOf(el); if (r) out.role = r;
        if (!isVisible(el)) out.visible = false;
        if (isDisabled(el)) out.disabled = true;
        if (inShadow.has(el)) out.shadow = true;
        return out;
    }

    // 表单字段的标签：只用 HTML 语义，不猜类名。
    // 1. <label for="id">  2. 外层 <label>/<fieldset>  3. 前一个兄弟节点的文本
    function labelFor(el) {
        if (el.id) {
            const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
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

    // 组件识别用的祖先链：只留带组件库类名或业务类名的层级。
    // uid 让服务端能把「同一个容器里的输入框」精确分组——按类名分组会把页面上
    // 两个一模一样的区间选择器合成一个，槽位于是全部解析到第一个控件上。
    function componentAncestors(el) {
        const out = [];
        let cur = parentOf(el), depth = 0;
        while (cur && cur.nodeType === 1 && cur !== document.documentElement && depth++ < 10 && out.length < 6) {
            const classes = classesOf(cur);
            if (classes.length && (classes.some(c => FRAMEWORK_RE.test(c)) || classes.some(isBusinessClass))) {
                out.push({ uid: uid(cur), classes: classes.slice(0, 12), selector: bestSelector(cur).selector });
            }
            cur = parentOf(cur);
        }
        return out;
    }

    // 容器链不筛类名，逐层如实记 uid 与该层身份：只留有类名的祖先，<form id=x> 这类一层证据
    // 都留不下，服务端就没有东西可以判断两个日期框是不是同一个控件的两端。
    // ident 区分「同一控件两端各套一层同样包装」和「两个区域各有一个日期框」：前者两层身份
    // 相同，后者不同（#search vs #edit）。body 谁都装得下，算进去等于全页配对。
    function containerChain(el) {
        const out = [];
        let cur = parentOf(el), depth = 0;
        while (cur && cur.nodeType === 1 && cur !== document.body
               && cur !== document.documentElement && depth++ < 10) {
            const cls = classesOf(cur);
            const id = (cur.id && !/^\d/.test(cur.id)) ? '#' + cur.id : '';
            const ident = id || (cls.length ? '.' + cls.slice().sort().join('.') : null);
            out.push({ uid: uid(cur), ident: ident, selector: ident ? bestSelector(cur).selector : null });
            cur = parentOf(cur);
        }
        return out;
    }

    // 同文本控件不能整段丢掉：丢掉等于告诉模型「页面上只有一个查询按钮」，
    // 它就会写出命中第一行的 selector。每组最多留 perGroup 个，并如实报出该组总数。
    function limitByGroup(els, keyOf, perGroup) {
        const groups = new Map();
        for (const el of els) {
            const k = keyOf(el);
            if (!groups.has(k)) groups.set(k, []);
            groups.get(k).push(el);
        }
        const kept = [];
        for (const [, list] of groups) {
            for (const el of list.slice(0, perGroup)) kept.push({ el: el, group_size: list.length });
        }
        return kept;
    }

    // ── 探测范围：scope 找不到就报错，绝不回退整页 ────────────────
    // 回退整页的结果会被当成「这个区域里的元素」，模型据此写出的 selector
    // 命中的是页面别处的同名控件，而它看不出来自己看错了区域。
    let scopeEl = null;
    let scopeMatches = 0;
    if (scopeSelector) {
        const found = queryAll(DOC_ROOTS, scopeSelector);
        scopeMatches = found.length;
        if (!found.length) {
            return {
                url: window.location.href,
                title: document.title,
                observation_version: version,
                scope_selector: scopeSelector,
                scope_missing: true,
                error: 'scope_selector 在当前页面上没有命中任何元素，未回退到整页探测。',
                required_action: 'retry_without_scope_or_fix_selector',
                inputs: [], selects: [], buttons: [], links: [], tables: [],
                visible_options: [], page_classes: [], all_classes: [], page_layout: [],
            };
        }
        if (found.length > 1) {
            // 只报命中几个，模型手上没有可选的东西，收窄只能再猜一个 selector 再撞一次。
            // 候选带上各自的唯一选择器与首段文本，它才能直接挑中那一个容器。
            return {url: window.location.href, observation_version: version,
                scope_selector: scopeSelector, scope_matches: found.length,
                scope_candidates: found.slice(0, 8).map(el => {
                    const bs = bestSelector(el);
                    const cand = {selector: bs.selector, tag: el.tagName.toLowerCase(), text: renderedText(el)};
                    if (bs.matches !== 1) cand.matches = bs.matches;
                    return cand;
                }),
                error: 'scope_selector 命中多个容器，请收窄范围；未选择第一个容器。'
                    + '候选容器见 scope_candidates，从中挑一个改写 scope_selector。',
                required_action: 'narrow_scope_selector'};
        }
        scopeEl = found[0];
    }
    const ROOTS = scopeEl ? collectRoots(scopeEl, []) : DOC_ROOTS;

    // ── 表单字段 ──────────────────────────────────────────────────
    // 有 placeholder/name/id 的输入框排在无标识的前面：截断时先保住可定位的那些。
    const inputEls = queryAll(ROOTS, 'input:not([type=hidden]), textarea');
    const inputs = inputEls.map(el => {
        const out = base(el);
        out.tag = el.tagName.toLowerCase();
        out.type = el.type || null;
        out.name = el.getAttribute('name') || null;
        out.id = el.id || null;
        out.placeholder = el.getAttribute('placeholder') || null;
        out.label = labelFor(el);
        const nm = accName(el);
        if (nm && nm !== out.label && nm !== out.placeholder) out.name_hint = nm;
        // readonly 输入框通常是「点开弹层选择」的组件触发器而非可自由键入的文本框；
        // value 暴露该控件真实接受的文本格式（如 2026-06-01 / 2026/06/01），
        // 二者都与组件库无关，未知框架也能据此判断该怎么交互。
        if (el.readOnly) out.readonly = true;
        out.value = el.type === 'password' ? null : ((el.value || '').slice(0, 40) || null);
        out.ancestors = componentAncestors(el);   // 仅服务端做组件识别用，返回给模型前会被移除
        out.containers = containerChain(el);      // 同上，仅服务端做日期控件实例分组
        return out;
    }).sort((a, b) => (a.placeholder || a.name || a.id ? 0 : 1) - (b.placeholder || b.name || b.id ? 0 : 1))
      .slice(0, MAX);

    const selects = queryAll(ROOTS, 'select').slice(0, 20).map(el => {
        const out = base(el);
        out.name = el.getAttribute('name') || null;
        out.id = el.id || null;
        out.label = labelFor(el);
        out.options = [...el.options].map(o => o.text.trim()).filter(Boolean).slice(0, 20);
        out.ancestors = componentAncestors(el);
        return out;
    });

    // ── 按钮：重复的同文本按钮必须暴露出来 ────────────────────────
    function nearestBizAncestor(el) {
        let cur = parentOf(el);
        while (cur && cur !== document.body) {
            const bizCls = classesOf(cur).find(isBusinessClass);
            if (bizCls) return { el: cur, cls: bizCls };
            cur = parentOf(cur);
        }
        return null;
    }
    const buttonEls = queryAll(ROOTS, 'button, input[type=submit], input[type=button], [role=button], a:not([href])')
        .filter(el => {
            const t = text(el);
            if (!t) return false;
            // 没有 href 的 <a> 是「靠 JS 监听器点」的伪按钮：分页页码、tab、日期格多半是这个形状。
            // links 只收 a[href]，不把它们收进来，模型就看不见页码，也就判不出「页码到底变没变」，
            // 只能照类名猜一个 .page-num 再盲选 nth。只收短文本：整块内容包在 <a> 里的卡片不是按钮。
            if (el.tagName === 'A' && !el.hasAttribute('href')) return t.length <= 16;
            return true;
        });
    const buttons = limitByGroup(buttonEls, el => text(el), 3).slice(0, MAX).map(item => {
        const el = item.el;
        const out = base(el);
        out.text = text(el);
        out.type = el.type || null;
        // 分页页码、tab 的「当前选中」只写在 class 上（active / current / selected）：
        // 不给出来，模型无法判断这次点击有没有真的换页
        out.cls = String(el.className || '').slice(0, 60) || null;
        if (item.group_size > 1) {
            // 同文本按钮有几个，模型必须知道：页面上 12 个「编辑」时，
            // 裸文本选择器命中的是第一行，而任务要点的是目标那一行。
            out.same_text_count = item.group_size;
            const anc = nearestBizAncestor(el);
            if (anc) out.container = bestSelector(anc.el).selector;
        }
        return out;
    });

    const linkEls = queryAll(ROOTS, 'a[href]').filter(el => text(el).length > 0);
    const links = limitByGroup(linkEls, el => text(el), 2).slice(0, MAX).map(item => {
        const out = base(item.el);
        out.text = text(item.el);
        out.href = item.el.href || null;
        out.cls = String(item.el.className || '').slice(0, 60);
        if (item.group_size > 1) out.same_text_count = item.group_size;
        return out;
    });

    // ── 表格：标准 HTML + ARIA grid/table，外加渲染出表头行的自研组件 ──
    // 返回命中的分支名而不只是选择器：模型据此知道这条路径是按哪种结构推出来的，
    // 自研组件那两支（biz_row_class / biz_scope_tr）本就是猜，写进流程前该回页面验一次。
    function bizRowSelector(tbl) {
        const tableScope = bestSelector(tbl).selector;
        // 判据是「有没有 tbody」而不是「tbody 里现在有没有行」：后者让同一张表在空的时候
        // 退到 ' tr'（连表头行一起圈进去），有数据时才收窄，推荐给流程的选择器随观察时刻变。
        if (tbl.tagName === 'TABLE' && tbl.tBodies.length > 0) {
            return { selector: tableScope + ' > tbody > tr', source: 'native_tbody' };
        }
        if (tbl.matches('[role=grid], [role=table]') && tbl.querySelector('[role=row]')) {
            return { selector: tableScope + ' [role=row]:has([role=cell], [role=gridcell])', source: 'aria_row' };
        }
        // tbody 缺失的 <table>：DOM API 往 <table> 上直接 append <tr> 不会补 tbody
        // （HTML 解析器才补），上面那支落空，行仍然在 tr 里。
        if (tbl.tagName === 'TABLE' && tbl.querySelector('tr')) {
            return { selector: tableScope + ' tr', source: 'table_tr' };
        }
        const anc = nearestBizAncestor(tbl);
        if (!anc) return { selector: null, source: null };
        const scope = '.' + CSS.escape(anc.cls);
        const rowEl = tbl.querySelector('[class*="row"], [class*="__row"], [class*="-row"]');
        if (rowEl) {
            const rowCls = [...rowEl.classList].find(c =>
                /row|__row|-row|--row/.test(c) && !isLayoutOnly(c)
            );
            if (rowCls) return { selector: scope + ' .' + CSS.escape(rowCls), source: 'biz_row_class' };
        }
        return { selector: scope + ' tr', source: 'biz_scope_tr' };
    }

    // 表头行只按 DOM 结构判，不看文本：拿「像表头的字」当判据，第一行数据叫「合计」
    // 就会被当表头摘掉，而真表头用了数据样的词就会混进样例行。
    // 行标题（<th scope="row"> / role=rowheader）不算列标题单元格：它长在数据行上，
    // 认了它，第一条数据就会被当成表头。
    function isColumnHeaderCell(c) {
        if (c.getAttribute('role') === 'rowheader') return false;
        if (c.tagName === 'TH' && c.getAttribute('scope') === 'row') return false;
        return c.tagName === 'TH' || c.getAttribute('role') === 'columnheader';
    }

    function isHeaderRow(row) {
        if (row.closest('thead')) return true;
        const cells = [...row.children];
        if (cells.length === 0) return false;
        return cells.every(isColumnHeaderCell);
    }

    // 空列位置保留 ''：塌掉空列，第 3 列的值会顶到第 2 列上，模型据此把字段映射整体错一位。
    function rowCells(row) {
        return [...row.children].map(c => text(c));
    }

    // 列标题只从表头行取：把整张表的 th 一起收进来，混合表格会把首列的行标题
    // 「华东/华南/华北」也算成列标题，多出三列，字段映射整体错位。
    function headerRow(tbl) {
        const rows = [...tbl.querySelectorAll('tr, [role=row]')];
        const head = rows.find(isHeaderRow);
        if (head) return head;
        // 有行、但没有一行是表头行 = 这张表没有列标题，如实交空数组，不退回「第一个 th 的父元素」
        if (rows.length) return null;
        // 自研组件可能一个行标记都不给（既没有 tr 也没有 [role=row]），这时才退回列标题
        // 单元格的直接父元素——仍是结构判据，不看类名。
        const cell = [...tbl.querySelectorAll('th, [role=columnheader]')].find(isColumnHeaderCell);
        const parent = cell && cell.parentElement;
        return parent && parent !== tbl ? parent : null;
    }

    // row_count 少算，合法空表与「选择器没命中」就分不开；样例行混进表头行，
    // 字段映射会拿表头文字当第一条数据。
    function rowEvidence(selector) {
        if (!selector) return { row_count: null, sample_rows: [] };
        let matched;
        try { matched = queryAll(DOC_ROOTS, selector); } catch (e) { return { row_count: null, sample_rows: [] }; }
        const dataRows = matched.filter(r => !isHeaderRow(r));
        return { row_count: dataRows.length, sample_rows: dataRows.slice(0, 2).map(rowCells) };
    }

    const tableElSet = new Set(queryAll(ROOTS, 'table, [role=grid], [role=table]'));
    queryAll(ROOTS, '[class]:not(table)').forEach(el => {
        // 标准表格的祖先只是布局容器，不是另一张表。
        if (el.querySelector('table, [role=grid], [role=table]') || el.closest('table, [role=grid], [role=table]')) return;
        if (el.querySelector('th, [role=columnheader]')) tableElSet.add(el);
    });
    const tables = [...tableElSet].slice(0, 5).map(tbl => {
        const row = bizRowSelector(tbl);
        const head = headerRow(tbl);
        const container = bestSelector(tbl).selector;
        const evidence = rowEvidence(row.selector);
        return {
            headers: head ? rowCells(head) : [],
            // 叫 container_selector 而不是 selector：browser.extract 要的是行路径，
            // 而名字里带 selector 的字段会被原样抄进去。
            container_selector: container,
            // 等待与提取分成两个字段：共用一个时模型会拿行选择器去等，
            // 合法空表上永远等不到，流程停在超时而不是交出空结果。
            ready_selector: container,
            cls: String(tbl.className || '').slice(0, 60),
            row_selector: row.selector,
            row_selector_source: row.source,
            ...evidence,
            // 有表头、零数据行 = 合法空表，等待到此为止；row_count 为 null 是没数出来
            // （没有行选择器或选择器非法），那是证据缺失，不能当成空表放行。
            empty_state: head !== null && evidence.row_count === 0,
            ref: ref(tbl),
        };
    });

    // ── 当前已展开的浮层 ──────────────────────────────────────────
    // 组件库的下拉/日历面板几乎都挂在 body 下、绝对定位；这是「点开控件后下一次观察能看到
    // 面板」的唯一客观依据。自研面板的日期格常是没有 href 的 <a>/<div>，links 只收 a[href]、
    // buttons 只收 button/[role=button]，只按 ARIA/li/td 判浮层它们一个都不出现，模型只能照
    // 类名猜格子再靠 nth 盲选。判据取「同类名重复出现的可点叶子」：带 href 的链接和按钮内部
    // 的文字节点不算，固定定位的导航条靠这一条排除（它每个链接都有 href）。
    function panelCells(el) {
        const groups = new Map();
        for (const node of el.querySelectorAll('*')) {
            if (node.children.length) continue;                        // 只要叶子，容器不算格子
            if (node.closest('a[href], button, [role=button], input, select, textarea')) continue;
            const t = text(node);
            if (!t || t.length > 16) continue;                         // 格子文字短；长文本是说明不是选项
            const key = (node.classList[0] || node.tagName).toLowerCase();
            const bucket = groups.get(key) || [];
            bucket.push(node);
            groups.set(key, bucket);
        }
        let best = [];
        for (const bucket of groups.values()) {
            if (bucket.length > best.length) best = bucket;
        }
        return best.length >= 3 ? best.slice(0, 40) : [];
    }
    const openLayers = [];
    const layerCells = [];
    for (const el of [...document.body.children]) {
        if (openLayers.length >= 4) break;
        if (!(el instanceof Element) || !isVisible(el)) continue;
        const pos = getComputedStyle(el).position;
        if (pos !== 'absolute' && pos !== 'fixed') continue;
        const optionCount = el.querySelectorAll('[role=option], [aria-selected], li').length;
        const cellCount = el.querySelectorAll('td, [role=gridcell]').length;
        const role = el.getAttribute('role');
        const cells = panelCells(el);
        if (!optionCount && !cellCount && !cells.length && role !== 'dialog' && role !== 'tooltip') continue;
        cells.forEach(c => layerCells.push(c));
        openLayers.push({
            ref: ref(el),
            selector: bestSelector(el).selector,
            role: role || null,
            classes: classesOf(el).slice(0, 8),
            option_count: optionCount || null,
            cell_count: cellCount || null,
            text_sample: text(el).slice(0, 160) || null,
        });
    }

    // ── 当前可见的选项（ARIA + 浮层内） ───────────────────────────
    // 只有下拉/列表真的展开时才有内容；带上 selector，模型才能在同一次会话里点中它。
    const optionEls = [...document.querySelectorAll('[role=option], [aria-selected]')].filter(el => text(el));
    for (const cell of layerCells) {
        if (!optionEls.includes(cell)) optionEls.push(cell);
    }
    const visibleOptions = optionEls
        .slice(0, 40)
        .map(el => ({ text: text(el), ref: ref(el), selector: bestSelector(el).selector }));

    // ── 页面全部 class 名 ─────────────────────────────────────────
    // 组件库指纹常常出现在 DOM 靠后的位置（筛选区、下拉浮层），按文档序截断会把它们整段丢掉，
    // 组件识别就永远匹配不上。所以：截断只用于给模型看，识别一律用全量集合。
    const classSet = new Set();
    document.querySelectorAll('[class]').forEach(el =>
        String(el.className).split(/\s+/).forEach(c => {
            if (c.length > 2 && c.length < 40) classSet.add(c);
        })
    );
    const allCls = [...classSet];
    const pageCls = allCls.slice(0, 120);

    // 闭合 shadow root 与「根本没有 shadow root」在页面脚本里完全无法区分，
    // 所以只能把可疑的自定义元素报出去，不能宣称支持闭合 shadow DOM。
    const customTags = new Set();
    document.querySelectorAll('*').forEach(el => {
        const tag = el.tagName.toLowerCase();
        if (tag.includes('-') && !el.shadowRoot && el.children.length === 0 && customTags.size < 8) customTags.add(tag);
    });

    // ── 页面骨架：顶层结构元素 + 其 HTML ──────────────────────────
    // 不预设分类。取 body 直接子节点；SPA 外壳把整页包在 1-2 个 div 里时再往下一层。
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
    function safeLayoutHtml(el) {
        const clone = el.cloneNode(true);
        clone.querySelectorAll('script').forEach(node => node.remove());
        const secrets = [...clone.querySelectorAll('input[type="password"]')];
        if (clone.matches('input[type="password"]')) secrets.push(clone);
        secrets.forEach(node => node.removeAttribute('value'));
        return clone.outerHTML.slice(0, 2000);
    }
    const pageLayout = structuralEls.map(el => ({
        tag: el.tagName.toLowerCase(),
        cls: String(el.className || '').slice(0, 80),
        role: el.getAttribute('role') || null,
        id: el.id || null,
        aria_label: el.getAttribute('aria-label') || null,
        html: safeLayoutHtml(el),
    }));

    // ── 整页 HTML（按需） ─────────────────────────────────────────
    // 给后端做精简正文用。默认不带：interact_page 每次动作后都会重新观察一次，
    // 每次都搬整页 HTML 过 WebSocket，绝大多数时候那份正文根本没人读。
    //
    // 只做两件脱敏，与 safeLayoutHtml 同一套：去掉 script（内容是噪声，且不该外传），
    // 抹掉 password 的 value（凭据一律不出页面）。「哪些算正文」不在这里判——
    // 清理与分块由后端单点做，这里再判一次就会有两处各自决定什么是噪声。
    //
    // 超限不截断：HTML 从中间断开后，解析器的 recover 模式会静默产出一棵看似正常
    // 的树，模型据此写出的 selector 在真实页面上不存在。所以只报为什么没带。
    function capturePageHtml() {
        const MAX_HTML = 2 * 1024 * 1024;
        const root = document.documentElement;
        if (!root) return { unavailable: { reason: 'no_document_element' } };
        const raw = root.outerHTML || '';
        if (raw.length > MAX_HTML) {
            return { unavailable: { reason: 'page_html_too_large', length: raw.length, limit: MAX_HTML } };
        }
        const clone = root.cloneNode(true);
        clone.querySelectorAll('script').forEach(node => node.remove());
        clone.querySelectorAll('input[type="password"]').forEach(node => node.removeAttribute('value'));
        return { html: clone.outerHTML };
    }

    const result = {
        url: window.location.href,
        title: document.title,
        observation_version: version,
        inputs,
        selects,
        buttons,
        links,
        tables,
        open_layers: openLayers,
        visible_options: visibleOptions,
        page_classes: pageCls,
        all_classes: allCls,   // 仅供服务端做组件识别/加载态判断，返回给模型前会被移除
        page_layout: pageLayout,
    };
    if (includeHtml) {
        const captured = capturePageHtml();
        // page_html 仅供服务端建正文快照，返回给模型前会被摘掉（它看的是精简后的正文）。
        if (captured.html !== undefined) result.page_html = captured.html;
        else result.page_html_unavailable = captured.unavailable;
    }
    if (scopeSelector) {
        result.scope_selector = scopeSelector;
        if (scopeMatches > 1) result.scope_matches = scopeMatches;
    }
    const openShadow = DOC_ROOTS.filter(r => r.host).length;
    if (openShadow) result.shadow_open_roots = openShadow;
    if (customTags.size) result.undetectable_custom_elements = [...customTags];
    return result;
};
