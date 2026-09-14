import { containerSelectorPrefixes } from './dom';
import type { ExtractedTableRow } from './types';

const TABLE_ROW_SELECTOR = 'tr,[role="row"]';
const TABLE_SELECTOR = '[role="grid"],[role="table"],table';
// 行标题（role=rowheader）长在数据行上，必须算作数据单元格：漏掉它那张表就少一列，
// 后面每个值整体左移一位顶到别的字段名下。与 Playwright 侧 _TABLE_EXTRACT_SCRIPT 同一份判据。
const TABLE_CELL_SELECTOR = 'td,th,[role="cell"],[role="gridcell"],[role="columnheader"],[role="rowheader"]';
const HEADER_CELL_SELECTOR = 'th,[role="columnheader"]';
// 「这张表里有数据」的判据。漏掉 role=cell，用它的 ARIA 表永远不算数据表，
// 多张表圈在一起时不报错而是静默合并。
const DATA_CELL_SELECTOR = 'td,[role="cell"],[role="gridcell"]';

interface HeaderReadOptions {
  allowFirstRowFallback?: boolean;
}

function normalizeCellText(el: Element): string {
  return (el.textContent ?? '').replace(/\s+/g, ' ').trim();
}

function uniqueHeaders(rawHeaders: string[]): string[] {
  const seen = new Map<string, number>();
  return rawHeaders.map((rawHeader, index) => {
    const base = rawHeader.trim() || `列${index + 1}`;
    const count = seen.get(base) ?? 0;
    seen.set(base, count + 1);
    return count === 0 ? base : `${base}_${count + 1}`;
  });
}

function readRowCells(row: Element): string[] {
  const directCells = Array.from(row.children).filter((child) => child.matches(TABLE_CELL_SELECTOR));
  const cells =
    directCells.length > 0
      ? directCells
      : Array.from(row.querySelectorAll(TABLE_CELL_SELECTOR)).filter((cell) => cell.closest(TABLE_ROW_SELECTOR) === row || cell.parentElement === row);
  if (cells.length === 0) {
    const text = normalizeCellText(row);
    return text === '' ? [] : [text];
  }
  // 空单元格保留为 ''：丢掉会让后续单元格整体左移一位，与表头数组错位。
  return cells.map(normalizeCellText);
}

function hasHeaderCells(row: Element): boolean {
  // 没有 thead 时才问这一行「像不像表头」，所以是 some 不是 every（混合 th/td 的表头行
  // 只有首列是 th）。但行标题不算：<th scope="row"> 长在数据行上，认了它，首列是行标题
  // 的第一条数据会被整行当成列标题交出去（headers=["East","12"]）。
  return Array.from(row.children).some(isColumnHeaderCell);
}

function readTableHeaders(root: Element, options: HeaderReadOptions = {}): string[] {
  const allowFirstRowFallback = options.allowFirstRowFallback ?? true;
  const tableRoot = root.closest(TABLE_SELECTOR) ?? root;
  const theadHeaders = Array.from(
    tableRoot.querySelectorAll('thead th,[role="columnheader"]'),
  )
    .map(normalizeCellText);
  if (theadHeaders.length > 0) return uniqueHeaders(theadHeaders);
  if (!allowFirstRowFallback) return [];

  const firstRow = tableRoot.querySelector(TABLE_ROW_SELECTOR);
  if (firstRow === null) return [];
  if (!hasHeaderCells(firstRow)) return [];
  const cells = readRowCells(firstRow);
  return uniqueHeaders(cells);
}

// 行标题（<th scope="row"> / role=rowheader）长在数据行上，不算列标题单元格：
// 认了它，首列是行标题的那条数据整行会被当表头摘掉。
function isColumnHeaderCell(cell: Element): boolean {
  if (cell.getAttribute('role') === 'rowheader') return false;
  if (cell.tagName === 'TH' && cell.getAttribute('scope') === 'row') return false;
  return cell.matches(HEADER_CELL_SELECTOR);
}

function isHeaderRow(row: Element): boolean {
  if (row.closest('thead') !== null) return true;
  const cells = Array.from(row.children).filter((child) => child.matches(TABLE_CELL_SELECTOR));
  return cells.length > 0 && cells.every(isColumnHeaderCell);
}

// 「这是一片数据区域」的结构证据，与观察侧 page_probe.js 的 empty_state 用同一条判据
// （有表头行 + 零数据行）。两边判据不一致时，同一张表会观察说空表、提取说选择器错。
function hasColumnHeaderRow(table: Element): boolean {
  return Array.from(table.querySelectorAll(TABLE_ROW_SELECTOR)).some(isHeaderRow);
}

function buildTableRow(cells: string[], headers: string[]): ExtractedTableRow | null {
  if (cells.length === 0) return null;
  if (headers.length >= cells.length) {
    const row: Record<string, string> = {};
    cells.forEach((value, index) => {
      row[headers[index] ?? `列${index + 1}`] = value;
    });
    return row;
  }
  return cells;
}

function extractRowsFromTable(table: Element): ExtractedTableRow[] {
  const headers = readTableHeaders(table);
  const rows = Array.from(table.querySelectorAll(':scope > tbody tr, :scope > tr, [role="row"]'));
  const sourceRows = rows.length > 0 ? rows : Array.from(table.querySelectorAll(TABLE_ROW_SELECTOR));
  return sourceRows
    .filter((row) => !isHeaderRow(row))
    .map((row) => buildTableRow(readRowCells(row), headers))
    .filter((row): row is ExtractedTableRow => row !== null);
}

// 与 browser_action_runner.py 的 _TABLE_EXTRACT_SCRIPT 同形：Python 侧那份
// _raise_if_table_scope_error 认这两个码，两条执行通道共用同一段报错文案。
export interface TableScopeError {
  __table_scope_error: 'no_rows_in_scope' | 'multiple_tables_in_scope';
  tableCount?: number;
}

export function extractTableRows(elements: Element[]): ExtractedTableRow[] | TableScopeError {
  const rows: ExtractedTableRow[] = [];
  const seenTables = new Set<Element>();
  const rowOwners = new Set<Element>();

  for (const element of elements) {
    const tag = element.tagName.toLowerCase();
    if (tag === 'table') {
      if (!seenTables.has(element)) {
        seenTables.add(element);
        rows.push(...extractRowsFromTable(element));
      }
      continue;
    }

    if (element.matches(TABLE_ROW_SELECTOR)) {
      if (isHeaderRow(element)) continue;
      const root = element.closest(TABLE_SELECTOR);
      if (root !== null) rowOwners.add(root);
      const headers = root === null ? [] : readTableHeaders(root, { allowFirstRowFallback: false });
      const row = buildTableRow(readRowCells(element), headers);
      if (row !== null) rows.push(row);
      continue;
    }

    const nestedTables = Array.from(element.querySelectorAll('table'));
    if (nestedTables.length > 0) {
      for (const table of nestedTables) {
        if (seenTables.has(table)) continue;
        seenTables.add(table);
        rows.push(...extractRowsFromTable(table));
      }
      continue;
    }

    const nestedRows = Array.from(element.querySelectorAll(TABLE_ROW_SELECTOR));
    for (const rowElement of nestedRows) {
      if (isHeaderRow(rowElement)) continue;
      const root = rowElement.closest(TABLE_SELECTOR);
      if (root !== null) rowOwners.add(root);
      const headers = root === null ? [] : readTableHeaders(root, { allowFirstRowFallback: false });
      const row = buildTableRow(readRowCells(rowElement), headers);
      if (row !== null) rows.push(row);
    }
  }

  // 跨表只按数据表算：Element UI 会把表头拆成独立的纯 th 表格，那不是另一份数据。
  const dataOwners = [...seenTables, ...rowOwners].filter(
    (table) => table.querySelector(DATA_CELL_SELECTOR) !== null,
  );
  if (dataOwners.length > 1) {
    return { __table_scope_error: 'multiple_tables_in_scope', tableCount: dataOwners.length };
  }
  if (rows.length === 0) {
    // 零行有两种，出路相反：
    // 「圈到了一片有表头的数据区域、此刻就是没有数据」= 合法空表，照常交零行——这与
    // 观察侧 empty_state=true 是同一条判据，报成范围错会让模型去改一个本来就对的选择器。
    // 「什么表格都没圈到」才是选择器没命中：交空数组会被当成「这张表此刻没有数据」，
    // 流程照常成功，交出来的是一份空结果。
    const framed = [...seenTables, ...rowOwners].some(hasColumnHeaderRow);
    if (framed) return [];
    return { __table_scope_error: 'no_rows_in_scope' };
  }

  return rows;
}

// 行选择器打在合法空表上一个元素都命中不到（thead 还在、tbody 已清空），此时能证明
// 「表在、只是没数据」的只剩容器：按后代前缀逐级收缩，取最贴近的那一级。
// 抛「未找到元素」会把合法空表当成执行失败，返回 [] 又会把选择器写错当成没有数据，
// 两者的出路相反，所以这里必须给出与 extractTableRows 同形的两态结论。
export function emptyScopeVerdict(
  selector: string,
  locate: (candidate: string) => Element[],
): ExtractedTableRow[] | TableScopeError {
  for (const prefix of containerSelectorPrefixes(selector)) {
    const hits = locate(prefix);
    if (hits.length === 0) continue;
    const framed = hits.some((el) => {
      const table = el.matches(TABLE_SELECTOR) ? el : el.closest(TABLE_SELECTOR) ?? el.querySelector(TABLE_SELECTOR);
      return table !== null && hasColumnHeaderRow(table);
    });
    return framed ? [] : { __table_scope_error: 'no_rows_in_scope' };
  }
  return { __table_scope_error: 'no_rows_in_scope' };
}
