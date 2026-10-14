import { isVisible, querySelectorAllDeep, readElementAttribute, resolveElement } from './dom';
import { extractTableRows, emptyScopeVerdict } from './tableExtract';
import type { TableScopeError } from './tableExtract';
import type { ContentAction, ExtractedTableRow } from './types';

export function dispatchExtract(
  action: Pick<ContentAction, 'selector' | 'ref' | 'extractMode' | 'attribute'>,
  includePageState = false,
): { text: string; values: Array<string | ExtractedTableRow> | TableScopeError; count: number; url?: string; busy?: boolean } {
  const elements =
    action.selector !== undefined && action.ref === undefined
      ? querySelectorAllDeep(action.selector)
      : [action.ref !== undefined ? resolveElement(action) : document.body];
  const pageState = includePageState ? {
    url: window.location.href,
    busy: document.readyState !== 'complete' || elements.some((el) => el.closest('[aria-busy="true"]') !== null),
  } : {};
  const mode = action.extractMode ?? 'text';
  if (mode === 'count') return { ...pageState, text: String(elements.length), values: [String(elements.length)], count: elements.length };
  if (mode === 'table') {
    // table 模式零元素不等于选择器写错，所以这一支排在「未找到元素」之前：行选择器打在
    // 合法空表上本就命中不到任何行，抛错会让后端把合法空表当成执行失败。
    const values =
      elements.length === 0 && action.selector !== undefined && action.ref === undefined
        ? emptyScopeVerdict(action.selector, querySelectorAllDeep)
        : extractTableRows(elements);
    // 圈错范围的标记原样交给后端裁决，count 记 0：在这里抛错，两条通道的报错文案会分家。
    if (!Array.isArray(values)) return { ...pageState, text: '', values, count: 0 };
    const text = values.map((row) => JSON.stringify(row)).join('\n');
    return { ...pageState, text, values, count: values.length };
  }
  if (elements.length === 0) throw new Error(`未找到元素: ${action.selector ?? action.ref ?? 'document.body'}`);
  if (mode === 'html') {
    const values = elements.map((item) => item.innerHTML.trim());
    return { ...pageState, text: values.join('\n'), values, count: values.length };
  }
  if (mode === 'attribute') {
    const attribute = action.attribute ?? 'href';
    const values = elements.map((item) => readElementAttribute(item, attribute)).filter((value) => value !== '');
    return { ...pageState, text: values.join('\n'), values, count: values.length };
  }
  const values = visibleTextValues(elements);
  return { ...pageState, text: values.join('\n'), values, count: values.length };
}

export function dispatchExtractAll(selector: string): { values: string[]; count: number } {
  const values = visibleTextValues(querySelectorAllDeep(selector));
  return { values, count: values.length };
}

function visibleTextValues(elements: Element[]): string[] {
  return elements.filter(isVisible)
    .map((el) => typeof (el as HTMLElement).innerText === 'string' ? (el as HTMLElement).innerText.trim() : '')
    .filter((value) => value !== '');
}
