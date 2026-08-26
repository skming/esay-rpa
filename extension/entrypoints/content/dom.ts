import type { ContentAction, DomElementSummary } from './types';

const INTERACTIVE_SELECTOR =
  'a, button, input, select, textarea, [role="button"], [role="link"], [role="checkbox"], ' +
  '[role="radio"], [role="tab"], [role="menuitem"], [role="option"], [contenteditable="true"], [onclick]';

let snapshotRefs = new Map<string, Element>();
let refCounter = 0;

function resetSnapshot(): void {
  snapshotRefs = new Map();
  refCounter = 0;
}

function assignRef(el: Element): string {
  refCounter += 1;
  const ref = `e${refCounter}`;
  snapshotRefs.set(ref, el);
  return ref;
}

function resolveRef(ref: string): Element {
  const el = snapshotRefs.get(ref);
  if (el === undefined || !el.isConnected) {
    throw new Error(`ref 已失效或不存在: ${ref}，请重新 query/find 生成快照`);
  }
  return el;
}

// 收集 document + open shadow root + 同源 iframe 供逐 root 匹配：原生 querySelector 不穿透 shadow/iframe，
// 组件库封装或支付 iframe 下裸 selector 会落空。
function collectRoots(root: Document | ShadowRoot, roots: (Document | ShadowRoot)[]): void {
  roots.push(root);
  const stack: Element[] = Array.from(root.children);
  while (stack.length > 0) {
    const el = stack.pop();
    if (el === undefined) continue;
    if (el.shadowRoot !== null) collectRoots(el.shadowRoot, roots);
    if (el.tagName === 'IFRAME') {
      try {
        const doc = (el as HTMLIFrameElement).contentDocument;
        if (doc !== null) collectRoots(doc, roots);
      } catch {
        // 跨域 iframe，无法访问，跳过
      }
    }
    stack.push(...Array.from(el.children));
  }
}

function splitSelectorList(selector: string): string[] {
  const parts: string[] = [];
  let current = '';
  let quote: string | null = null;
  let depth = 0;
  for (const char of selector) {
    if (quote !== null) {
      current += char;
      if (char === quote) quote = null;
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
      current += char;
      continue;
    }
    if (char === '(') depth += 1;
    if (char === ')') depth = Math.max(0, depth - 1);
    if (char === ',' && depth === 0) {
      if (current.trim() !== '') parts.push(current.trim());
      current = '';
      continue;
    }
    current += char;
  }
  if (current.trim() !== '') parts.push(current.trim());
  return parts;
}

function normalizePlaywrightSelector(selector: string): { css: string; texts: string[]; visible: boolean; innermost: boolean } {
  const texts: string[] = [];
  const textSelector = selector.match(/^text\s*=\s*(?:(["'])(.*?)\1|(.+))$/);
  let css = textSelector === null ? selector : '*';
  if (textSelector !== null) {
    texts.push((textSelector[2] ?? textSelector[3] ?? '').trim());
  }
  css = css.replace(/:has-text\(\s*(["'])(.*?)\1\s*\)/g, (_match, _quote, text: string) => {
    texts.push(text);
    return '';
  });
  css = css.replace(/:has-text\(\s*([^)]*?)\s*\)/g, (_match, text: string) => {
    texts.push(text.trim());
    return '';
  });
  const visible = /:visible\b/.test(css);
  css = css.replace(/:visible\b/g, '').trim();
  // text= 只命中含该文本的最深节点（不像 :has-text 连祖先一起中）；css 强制成 '*' 时不过滤会误中 body/html。
  return { css: css === '' ? '*' : css, texts, visible, innermost: textSelector !== null };
}

// 只保留最内层：排掉集合里其他元素的祖先（父子容器 textContent 常含同一段文本）。
function filterInnermost(elements: Element[]): Element[] {
  if (elements.length <= 1) return elements;
  return elements.filter((el) => !elements.some((other) => other !== el && el.contains(other)));
}

function elementMatchesText(el: Element, expectedTexts: string[]): boolean {
  if (expectedTexts.length === 0) return true;
  const text = (el.textContent ?? '').replace(/\s+/g, ' ').trim();
  return expectedTexts.every((expected) => text.includes(expected));
}

type SelectorCombinator = '' | ' ' | '>' | '+' | '~';

interface SelectorSegment {
  combinator: SelectorCombinator;
  css: string;
  texts: string[];
  visible: boolean;
  innermost: boolean;
}

// 按组合符（空格/`>`/`+`/`~`）切分复合选择器，跳过引号/括号内部——:has-text(...) 参数里的空格或 `>` 不算边界。
function splitCompoundSelector(selector: string): { combinator: SelectorCombinator; text: string }[] {
  const segments: { combinator: SelectorCombinator; text: string }[] = [];
  let current = '';
  let quote: string | null = null;
  let depth = 0;
  let pendingCombinator: SelectorCombinator | null = null;

  const flush = () => {
    if (current.trim() === '') return;
    segments.push({ combinator: pendingCombinator ?? (segments.length === 0 ? '' : ' '), text: current.trim() });
    current = '';
    pendingCombinator = null;
  };

  for (const char of selector) {
    if (quote !== null) {
      current += char;
      if (char === quote) quote = null;
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
      current += char;
      continue;
    }
    if (char === '(') depth += 1;
    if (char === ')') depth = Math.max(0, depth - 1);
    if (depth === 0 && (char === '>' || char === '+' || char === '~')) {
      flush();
      pendingCombinator = char;
      continue;
    }
    if (depth === 0 && /\s/.test(char)) {
      flush();
      continue;
    }
    current += char;
  }
  flush();
  return segments;
}

function buildSelectorSegments(selector: string): SelectorSegment[] {
  return splitCompoundSelector(selector).map(({ combinator, text }) => {
    const { css, texts, visible, innermost } = normalizePlaywrightSelector(text);
    return { combinator, css, texts, visible, innermost };
  });
}

function matchesSegmentFilter(el: Element, segment: SelectorSegment): boolean {
  if (segment.visible && !isVisible(el)) return false;
  return elementMatchesText(el, segment.texts);
}

function siblingsAfter(el: Element): Element[] {
  const parent = el.parentElement;
  if (parent === null) return [];
  const siblings = Array.from(parent.children);
  const index = siblings.indexOf(el);
  return index === -1 ? [] : siblings.slice(index + 1);
}

// `A:has-text("xxx") B` 的 Playwright 语义是"先按文本筛出祖先 A、再在其内找后代 B"，
// 故逐段推进匹配，而非剥离 :has-text 拼成一条扁平 CSS 统一过滤。
function querySelectorAllInRoot(root: Document | ShadowRoot, selector: string): Element[] {
  // 解构出首段再判 undefined，而不是判 segments.length：后面整段逻辑都建立在"第一段必然存在"
  // 之上，把这个前提写成一次判空，比每处下标访问各自断言一遍更难写错。
  const [firstSegment, ...restSegments] = buildSelectorSegments(selector);
  if (firstSegment === undefined) return [];

  try {
    let candidates: Element[] = Array.from(root.querySelectorAll(firstSegment.css)).filter((el) =>
      matchesSegmentFilter(el, firstSegment)
    );
    if (firstSegment.innermost) candidates = filterInnermost(candidates);

    for (const segment of restSegments) {
      const seen = new Set<Element>();
      const next: Element[] = [];
      for (const candidate of candidates) {
        let matched: Element[];
        if (segment.combinator === '>') {
          matched = Array.from(candidate.querySelectorAll(`:scope > ${segment.css}`));
        } else if (segment.combinator === '+') {
          const sibling = candidate.nextElementSibling;
          matched = sibling !== null && sibling.matches(segment.css) ? [sibling] : [];
        } else if (segment.combinator === '~') {
          matched = siblingsAfter(candidate).filter((sib) => sib.matches(segment.css));
        } else {
          matched = Array.from(candidate.querySelectorAll(segment.css));
        }
        for (const el of matched) {
          if (seen.has(el)) continue;
          if (!matchesSegmentFilter(el, segment)) continue;
          seen.add(el);
          next.push(el);
        }
      }
      candidates = segment.innermost ? filterInnermost(next) : next;
    }

    return candidates;
  } catch (error) {
    throw new Error(`不支持的选择器: ${selector} (${error instanceof Error ? error.message : String(error)})`);
  }
}

function querySelectorDeep(selector: string): Element | null {
  return querySelectorAllDeep(selector)[0] ?? null;
}

export function querySelectorAllDeep(selector: string): Element[] {
  const roots: (Document | ShadowRoot)[] = [];
  collectRoots(document, roots);
  const seen = new Set<Element>();
  const results: Element[] = [];
  for (const part of splitSelectorList(selector)) {
    for (const root of roots) {
      for (const el of querySelectorAllInRoot(root, part)) {
        if (seen.has(el)) continue;
        seen.add(el);
        results.push(el);
      }
    }
  }
  return results;
}

export function resolveElement(action: Pick<ContentAction, 'ref' | 'selector'>): Element {
  if (action.ref !== undefined) return resolveRef(action.ref);
  if (action.selector !== undefined) {
    const el = querySelectorDeep(action.selector);
    if (el === null) throw new Error(`未找到元素: ${action.selector}`);
    return el;
  }
  throw new Error('需要提供 ref 或 selector 之一');
}

// 同 resolveElement，但定位失败返回 null 而非抛错，供 elementState/ensureLogin 等探测型 action 使用。
export function tryResolveElement(action: Pick<ContentAction, 'ref' | 'selector'>): Element | null {
  try {
    return resolveElement(action);
  } catch {
    return null;
  }
}

// SPA 里登录态标记常预渲染但 display:none，只判"存在"会误判，故要求至少一个匹配元素可见。
export function probeSelectorVisible(selector: string): boolean {
  return querySelectorAllDeep(selector).some((el) => isVisible(el));
}

export function isVisible(el: Element): boolean {
  const rect = el.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return false;
  const style = window.getComputedStyle(el);
  return style.visibility !== 'hidden' && style.display !== 'none';
}

function labelTextFor(el: Element): string | null {
  if (el.id) {
    const label = (el.getRootNode() as ParentNode).querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (label !== null) return (label.textContent ?? '').trim() || null;
  }
  const closestLabel = el.closest('label');
  return closestLabel !== null ? (closestLabel.textContent ?? '').trim() || null : null;
}

function accessibleName(el: Element): string {
  const ariaLabel = el.getAttribute('aria-label');
  if (ariaLabel !== null && ariaLabel.trim() !== '') return ariaLabel.trim();

  const labelledBy = el.getAttribute('aria-labelledby');
  if (labelledBy !== null) {
    const root = el.getRootNode() as ParentNode;
    const labelled = labelledBy
      .split(/\s+/)
      .map((id) => root.querySelector(`#${CSS.escape(id)}`)?.textContent?.trim())
      .filter((text): text is string => Boolean(text))
      .join(' ');
    if (labelled !== '') return labelled;
  }

  const label = labelTextFor(el);
  if (label !== null) return label;

  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
    if (el.placeholder.trim() !== '') return el.placeholder.trim();
  }

  const alt = el.getAttribute('alt');
  if (alt !== null && alt.trim() !== '') return alt.trim();

  const title = el.getAttribute('title');
  if (title !== null && title.trim() !== '') return title.trim();

  return (el.textContent ?? '').trim();
}

export function readElementAttribute(el: Element, attribute: string): string {
  if (attribute === 'value' && (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement)) {
    return el.value;
  }
  const propertyValue = (el as unknown as Record<string, unknown>)[attribute];
  if ((attribute === 'href' || attribute === 'src' || attribute === 'action') && typeof propertyValue === 'string') {
    return propertyValue;
  }
  return el.getAttribute(attribute) ?? '';
}

function summarizeElement(el: Element): DomElementSummary {
  const rect = el.getBoundingClientRect();
  const name = accessibleName(el);
  return {
    ref: assignRef(el),
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute('role'),
    name: name.slice(0, 200),
    text: (el.textContent ?? '').trim().slice(0, 200),
    visible: isVisible(el),
    rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
  };
}

// DFS 穿透 open shadow root 和同源 iframe（跨域 iframe 跳过）：直接持有内部真实 Element，click/fill 无需跨 frame 传递。
function collectInteractiveElements(root: Document | ShadowRoot, results: Element[]): void {
  const stack: Element[] = Array.from(root.children);
  while (stack.length > 0) {
    const el = stack.pop();
    if (el === undefined) continue;
    if (el.matches(INTERACTIVE_SELECTOR)) results.push(el);
    if (el.shadowRoot !== null) collectInteractiveElements(el.shadowRoot, results);
    if (el.tagName === 'IFRAME') {
      try {
        const doc = (el as HTMLIFrameElement).contentDocument;
        if (doc !== null) collectInteractiveElements(doc, results);
      } catch {
        // 跨域 iframe，无法访问，跳过
      }
    }
    stack.push(...Array.from(el.children));
  }
}

export function captureSnapshot(): DomElementSummary[] {
  resetSnapshot();
  const elements: Element[] = [];
  collectInteractiveElements(document, elements);
  return elements
    .map(summarizeElement)
    .filter((summary) => summary.visible)
    .slice(0, 500);
}

function scoreCandidate(query: string, summary: DomElementSummary): number {
  const q = query.trim().toLowerCase();
  if (q === '') return 0;
  const name = summary.name.toLowerCase();
  const text = summary.text.toLowerCase();
  let score = 0;
  if (name === q || text === q) score += 100;
  if (name !== '' && name.includes(q)) score += 50;
  if (text !== '' && text.includes(q)) score += 30;
  if (name !== '' && q.includes(name)) score += 20;
  return score;
}

export function findElements(query: string, limit: number): DomElementSummary[] {
  const summaries = captureSnapshot();
  return summaries
    .map((summary) => ({ summary, score: scoreCandidate(query, summary) }))
    .filter(({ score }) => score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map(({ summary }) => summary);
}
