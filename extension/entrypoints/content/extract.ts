import { querySelectorAllDeep, readElementAttribute, resolveElement } from './dom';
import { extractTableRows } from './tableExtract';
import type { ContentAction, ExtractedTableRow } from './types';

export function dispatchExtract(
  action: Pick<ContentAction, 'selector' | 'ref' | 'extractMode' | 'attribute'>,
): { text: string; values: Array<string | ExtractedTableRow>; count: number } {
  const elements =
    action.selector !== undefined && action.ref === undefined
      ? querySelectorAllDeep(action.selector)
      : [action.ref !== undefined ? resolveElement(action) : document.body];
  const mode = action.extractMode ?? 'text';
  if (mode === 'count') return { text: String(elements.length), values: [String(elements.length)], count: elements.length };
  if (elements.length === 0) throw new Error(`未找到元素: ${action.selector ?? action.ref ?? 'document.body'}`);
  if (mode === 'html') {
    const values = elements.map((item) => item.innerHTML.trim());
    return { text: values.join('\n'), values, count: values.length };
  }
  if (mode === 'attribute') {
    const attribute = action.attribute ?? 'href';
    const values = elements.map((item) => readElementAttribute(item, attribute)).filter((value) => value !== '');
    return { text: values.join('\n'), values, count: values.length };
  }
  if (mode === 'table') {
    const values = extractTableRows(elements);
    const text = values.map((row) => JSON.stringify(row)).join('\n');
    return { text, values, count: values.length };
  }
  const values = elements.map((item) => (item.textContent ?? '').trim()).filter((value) => value !== '');
  return { text: values.join('\n'), values, count: values.length };
}

export function dispatchExtractAll(selector: string): { values: string[]; count: number } {
  const values = querySelectorAllDeep(selector).map((el) => (el.textContent ?? '').trim());
  return { values, count: values.length };
}
