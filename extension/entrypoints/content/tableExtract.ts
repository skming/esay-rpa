import type { ExtractedTableRow } from './types';

const TABLE_ROW_SELECTOR = 'tr,[role="row"]';
const TABLE_CELL_SELECTOR = 'td,th,[role="cell"],[role="gridcell"],[role="columnheader"]';
const HEADER_CELL_SELECTOR = 'th,[role="columnheader"]';

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
  return Array.from(row.children).some((child) => child.matches(HEADER_CELL_SELECTOR));
}

function readTableHeaders(root: Element, options: HeaderReadOptions = {}): string[] {
  const allowFirstRowFallback = options.allowFirstRowFallback ?? true;
  const tableRoot = root.closest('[role="grid"],[role="table"],table') ?? root;
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

function isHeaderRow(row: Element): boolean {
  if (row.closest('thead') !== null) return true;
  const cells = Array.from(row.children).filter((child) => child.matches(TABLE_CELL_SELECTOR));
  return cells.length > 0 && cells.every((cell) => cell.matches(HEADER_CELL_SELECTOR));
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

export function extractTableRows(elements: Element[]): ExtractedTableRow[] {
  const rows: ExtractedTableRow[] = [];
  const seenTables = new Set<Element>();

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
      const root = element.closest('[role="grid"],[role="table"],table');
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
      const root = rowElement.closest('[role="grid"],[role="table"],table');
      const headers = root === null ? [] : readTableHeaders(root, { allowFirstRowFallback: false });
      const row = buildTableRow(readRowCells(rowElement), headers);
      if (row !== null) rows.push(row);
    }
  }

  return rows;
}
