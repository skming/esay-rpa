/**
 * Playwright picker、扩展内容脚本和页面探测共用的原生 DOM 定位原语。
 *
 * 此文件只能使用浏览器原生 API：Python 会在导入时把该 factory 与入口表达式组合，
 * 扩展则直接按 ESM import。不要在这里加入 TypeScript 或第二个 module export。
 */
export const createDomLocator = (rootDocument = document) => {
    const FRAMEWORK_RE = /^(el|ant|arco|vxe|n|van|ivu|layui|semi|tdesign|varlet|vc|v)-/;
    const LAYOUT_WORDS = new Set([
        'app','page','main','layout','content','wrapper','container',
        'inner','outer','shell','frame','view','root','section',
        'area','panel','box','wrap','base','center','body','fluid','fixed','scroll',
    ]);

    function collectRoots(node, out) {
        out.push(node);
        let all = [];
        try { all = node.querySelectorAll('*'); } catch (e) { all = []; }
        for (const el of all) if (el.shadowRoot) collectRoots(el.shadowRoot, out);
        return out;
    }

    function queryAll(roots, sel) {
        const out = [];
        for (const root of roots) {
            try { root.querySelectorAll(sel).forEach(el => out.push(el)); } catch (e) { /* 非法选择器 */ }
        }
        return out;
    }

    const roots = collectRoots(rootDocument, []);
    const inShadow = new WeakSet();
    for (const root of roots) {
        if (root.host) {
            try { root.querySelectorAll('*').forEach(el => inShadow.add(el)); } catch (e) {}
        }
    }

    const countCache = new Map();
    function countDoc(sel) {
        if (countCache.has(sel)) return countCache.get(sel);
        const count = queryAll(roots, sel).length;
        countCache.set(sel, count);
        return count;
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
        for (const [name, value] of attrs) {
            const selector = attrSel(tag, name, value);
            if (selector) out.push(selector);
        }
        if (el.type && el.type !== 'text') {
            const selector = attrSel(tag, 'type', el.type);
            if (selector) out.push(selector);
        }
        return out;
    }

    function parentOf(el) {
        if (el.parentElement) return el.parentElement;
        const root = el.getRootNode();
        return root && root.host ? root.host : null;
    }

    // 结构路径：一定合法、一定唯一，两个执行器都能解析。停在最近的唯一 id 祖先。
    function cssPath(el) {
        const parts = [];
        let current = el;
        while (current && current.nodeType === 1 && parts.length < 8) {
            if (current.id && !/^\d/.test(current.id) && countDoc('#' + CSS.escape(current.id)) === 1) {
                parts.unshift('#' + CSS.escape(current.id));
                return parts.join(' > ');
            }
            const parent = current.parentElement;
            const tag = current.tagName.toLowerCase();
            if (!parent) { parts.unshift(tag); break; }
            const siblings = [...parent.children].filter(child => child.tagName === current.tagName);
            parts.unshift(siblings.length > 1 ? tag + ':nth-of-type(' + (siblings.indexOf(current) + 1) + ')' : tag);
            current = parent;
        }
        return parts.join(' > ');
    }

    function isLayoutOnly(cls) {
        const words = cls.toLowerCase().split(/[-_]/).filter(Boolean);
        return words.length > 0 && words.every(word => LAYOUT_WORDS.has(word));
    }

    function isBusinessClass(cls) {
        return cls.length > 2 && !FRAMEWORK_RE.test(cls) && !isLayoutOnly(cls);
    }

    function classesOf(el) {
        return String(el.className || '').split(/\s+/).filter(Boolean);
    }

    const selectorCache = new WeakMap();
    function bestSelector(el) {
        const cached = selectorCache.get(el);
        if (cached) return cached;
        let result = null;
        const stableCandidates = candidates(el);
        for (const selector of stableCandidates) {
            if (countDoc(selector) === 1) {
                result = { selector: selector, matches: 1, usesPosition: false };
                break;
            }
        }
        if (!result) {
            // 裸选择器不唯一时先用祖先限定，比 nth-of-type 路径可读，也不随兄弟节点增减而失效。
            for (const selector of stableCandidates) {
                let current = parentOf(el);
                let depth = 0;
                while (current && current.nodeType === 1 && depth++ < 6) {
                    const anchors = [];
                    if (current.id && !/^\d/.test(current.id)) anchors.push('#' + CSS.escape(current.id));
                    for (const cls of classesOf(current)) if (isBusinessClass(cls)) anchors.push('.' + CSS.escape(cls));
                    for (const anchor of anchors) {
                        const scoped = anchor + ' ' + selector;
                        if (countDoc(scoped) === 1) {
                            result = { selector: scoped, matches: 1, usesPosition: false };
                            break;
                        }
                    }
                    if (result) break;
                    current = parentOf(current);
                }
                if (result) break;
            }
        }
        if (!result) {
            const selector = cssPath(el);
            result = { selector: selector, matches: countDoc(selector), usesPosition: selector.includes(':nth-of-type(') };
        }
        selectorCache.set(el, result);
        return result;
    }

    function nearestBusinessAncestor(el) {
        let current = parentOf(el);
        while (current && current !== rootDocument.body) {
            const businessClass = classesOf(current).find(isBusinessClass);
            if (businessClass) return { el: current, cls: businessClass };
            current = parentOf(current);
        }
        return null;
    }

    function tableRowSelector(table) {
        const tableScope = bestSelector(table).selector;
        if (table.tagName === 'TABLE' && table.tBodies.length > 0) {
            return { selector: tableScope + ' > tbody > tr', source: 'native_tbody' };
        }
        if (table.matches('[role=grid], [role=table]') && table.querySelector('[role=row]')) {
            return { selector: tableScope + ' [role=row]:has([role=cell], [role=gridcell])', source: 'aria_row' };
        }
        if (table.tagName === 'TABLE' && table.querySelector('tr')) {
            return { selector: tableScope + ' tr', source: 'table_tr' };
        }
        const ancestor = nearestBusinessAncestor(table);
        if (!ancestor) return { selector: null, source: null };
        const scope = '.' + CSS.escape(ancestor.cls);
        const row = table.querySelector('[class*="row"], [class*="__row"], [class*="-row"]');
        if (row) {
            const rowClass = [...row.classList].find(cls => /row|__row|-row|--row/.test(cls) && !isLayoutOnly(cls));
            if (rowClass) return { selector: scope + ' .' + CSS.escape(rowClass), source: 'biz_row_class' };
        }
        return { selector: scope + ' tr', source: 'biz_scope_tr' };
    }

    function closestAncestor(el, predicate) {
        let current = el;
        while (current && current.nodeType === 1) {
            if (predicate(current)) return current;
            current = parentOf(current);
        }
        return null;
    }

    function semanticListItem(el) {
        return closestAncestor(el, current => {
            const tag = current.tagName.toLowerCase();
            const role = current.getAttribute('role');
            return tag === 'tr' || tag === 'li' || role === 'row' || role === 'listitem' || role === 'option';
        });
    }

    function listSelector(item) {
        const tag = item.tagName.toLowerCase();
        const role = item.getAttribute('role');
        if (tag === 'tr' || role === 'row') {
            const table = closestAncestor(item, current => current.matches('table, [role=grid], [role=table]'));
            if (table) return tableRowSelector(table).selector;
        }
        const list = closestAncestor(item, current => {
            const listTag = current.tagName.toLowerCase();
            const listRole = current.getAttribute('role');
            return listTag === 'ul' || listTag === 'ol' || listRole === 'list' || listRole === 'listbox';
        });
        if (!list) return null;
        const listScope = bestSelector(list).selector;
        const direct = parentOf(item) === list ? ' > ' : ' ';
        if (tag === 'li') return listScope + direct + 'li';
        if (role === 'listitem') return listScope + direct + '[role=listitem]';
        if (role === 'option') return listScope + direct + '[role=option]';
        return null;
    }

    function validateSelection(selector, target, selectionMode, usesPosition) {
        if (!selector) return { ok: false, reason: 'selector_not_found', matches: 0, selectedIncluded: false, usesPosition: false };
        const matched = queryAll(roots, selector);
        const selectedIncluded = selectionMode === 'single'
            ? matched.includes(target)
            : matched.some(el => el === target || el.contains(target));
        const valid = selectionMode === 'single'
            ? matched.length === 1 && selectedIncluded
            : matched.length >= 1 && selectedIncluded;
        return {
            ok: valid,
            reason: valid ? null : 'selector_validation_failed',
            selector: selector,
            matches: matched.length,
            selectedIncluded: selectedIncluded,
            usesPosition: usesPosition,
        };
    }

    function selectionFor(target, selectionMode) {
        if (!(target instanceof Element) || !target.isConnected) {
            return { ok: false, reason: 'selected_element_unavailable', matches: 0, selectedIncluded: false, usesPosition: false };
        }
        if (target.ownerDocument !== rootDocument || target.tagName === 'IFRAME') {
            return { ok: false, reason: 'frame_unsupported', matches: 0, selectedIncluded: false, usesPosition: false };
        }
        if (selectionMode === 'multiple') {
            const item = semanticListItem(target);
            if (item) {
                const selector = listSelector(item);
                if (selector) {
                    const validated = validateSelection(selector, target, selectionMode, selector.includes(':nth-of-type('));
                    if (validated.ok) return validated;
                }
            }
        }
        const result = bestSelector(target);
        return validateSelection(result.selector, target, selectionMode, result.usesPosition);
    }

    return {
        FRAMEWORK_RE,
        classesOf,
        collectRoots,
        inShadow,
        isBusinessClass,
        isLayoutOnly,
        nearestBusinessAncestor,
        parentOf,
        queryAll,
        roots,
        bestSelector,
        selectionFor,
        tableRowSelector,
    };
};
