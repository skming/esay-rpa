import { Globe, Lock, Pencil, Pin, PinOff, Workflow, X } from 'lucide-react';
import type { KeyboardEvent, ReactElement } from 'react';
import { useMemo, useState } from 'react';

import type { RuntimeVariableView } from '../../../types/rpa';
import { useFlowVariableStore } from '../../../stores/useFlowVariableStore';
import { Badge } from '../../ui/badge';
import { Button } from '../../ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../../ui/table';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '../../ui/tooltip';
import { cn } from '../../../lib/utils';
import { getScopeVariant, getVariableSourceTone } from './bottomPanelUtils';
import { TypeBadge } from './TypeBadge';

export function VariableRows({ rows }: { rows: RuntimeVariableView[] }): ReactElement {
  const updateInputVariable = useFlowVariableStore((s) => s.updateInputVariable);
  const [watchedNames, setWatchedNames] = useState<Set<string>>(() => new Set());
  const [editingName, setEditingName] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState('');

  const sortedRows = useMemo(
    () =>
      [...rows].sort((a, b) => {
        const aw = watchedNames.has(a.name);
        const bw = watchedNames.has(b.name);
        if (aw !== bw) return aw ? -1 : 1;
        return rows.indexOf(a) - rows.indexOf(b);
      }),
    [rows, watchedNames],
  );

  const toggleWatched = (name: string): void =>
    setWatchedNames((cur) => {
      const next = new Set(cur);
      next.has(name) ? next.delete(name) : next.add(name);
      return next;
    });

  const startEdit = (row: RuntimeVariableView): void => {
    setEditingName(row.name);
    setEditDraft(row.sensitive ? '' : row.value);
  };

  const commitEdit = (name: string): void => {
    updateInputVariable(name, { value: editDraft });
    setEditingName(null);
  };

  const cancelEdit = (): void => setEditingName(null);

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>, name: string): void => {
    if (e.key === 'Enter') { e.preventDefault(); commitEdit(name); }
    if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); }
  };

  if (rows.length === 0) {
    return (
      <div className="py-8 text-center text-[11px] text-slate-400">
        暂无变量。运行流程后变量将在此处显示。
      </div>
    );
  }

  const flowRows = sortedRows.filter((r) => (r.category ?? 'flow') === 'flow');
  const globalRows = sortedRows.filter((r) => r.category === 'environment' || r.category === 'credential');

  const renderRow = (row: RuntimeVariableView, isGlobal = false): ReactElement => {
    const watched = watchedNames.has(row.name);
    const isEditing = editingName === row.name;
    const canEdit = row.source === 'default';
    const sourceTone = getVariableSourceTone(row.source);
    const displayVal = row.sensitive ? '••••••••' : row.value;

    return (
      <TableRow
        className={cn(
          isEditing && 'bg-amber-50/50 ring-1 ring-inset ring-amber-200',
          !isEditing && watched && (isGlobal ? 'bg-accent-soft hover:bg-accent-soft' : 'bg-accent-soft hover:bg-accent-soft'),
          !isEditing && !watched && (isGlobal ? 'hover:bg-accent-soft' : ''),
        )}
        key={row.name}
      >
        {/* 变量名 */}
        <TableCell className="overflow-hidden pl-2 font-mono text-[11px] text-accent-strong">
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="flex min-w-0 items-center gap-1.5 cursor-default">
                {watched && <Pin className="h-3 w-3 shrink-0 text-accent" strokeWidth={1.5} />}
                <span className="block truncate">${`{${row.name}}`}</span>
              </span>
            </TooltipTrigger>
            <TooltipContent className="font-mono text-[11px]" side="top">
              ${`{${row.name}}`}
            </TooltipContent>
          </Tooltip>
        </TableCell>

        {/* 类型 */}
        <TableCell className="w-20">
          <TypeBadge type={row.type} />
        </TableCell>

        {/* 当前值 */}
        <TableCell className="overflow-hidden">
          {isEditing ? (
            <input
              autoFocus
              className="h-6 w-full rounded border border-amber-300 bg-white px-1.5 font-mono text-[11px] text-slate-800 outline-none focus:border-accent-linefocus:ring-1 focus:ring-accent-soft"
              onBlur={() => commitEdit(row.name)}
              onChange={(e) => setEditDraft(e.target.value)}
              onKeyDown={(e) => handleKeyDown(e, row.name)}
              placeholder={row.sensitive ? '输入新值…' : ''}
              type={row.sensitive ? 'password' : 'text'}
              value={editDraft}
            />
          ) : (
            <div className="flex min-w-0 items-center gap-1.5">
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className={cn('block min-w-0 truncate cursor-default font-mono text-[11px]', canEdit ? 'text-slate-700' : 'text-slate-400')}>
                    {displayVal !== '' ? displayVal : <span className="italic text-slate-300">空</span>}
                  </span>
                </TooltipTrigger>
                {displayVal !== '' && (
                  <TooltipContent className="max-w-120 break-all font-mono text-[11px] leading-5" side="top">
                    {displayVal}
                  </TooltipContent>
                )}
              </Tooltip>
              {!canEdit && (
                <span title={row.source === 'runtime' ? '运行时变量，只读' : '覆盖变量，只读'}>
                  <Lock className="h-3 w-3 shrink-0 text-slate-300" strokeWidth={1.5} />
                </span>
              )}
            </div>
          )}
        </TableCell>

        {/* 作用域 */}
        <TableCell className="w-22.5">
          <Badge className="w-fit" variant={getScopeVariant(row.scope)}>{row.scope}</Badge>
        </TableCell>

        {/* 来源 */}
        <TableCell className="w-47.5">
          <div className="flex min-w-0 flex-wrap items-center gap-1.5">
            <Badge className={cn('h-5 px-1.5 text-[10px]', sourceTone.badge)}>{sourceTone.label}</Badge>
            <Badge
              className="h-5 px-1.5 text-[10px]"
              variant={row.category === 'credential' ? 'red' : row.category === 'environment' ? 'amber' : 'default'}
            >
              {row.category === 'credential' ? '凭据' : row.category === 'environment' ? '环境' : '流程'}
            </Badge>
            {row.source !== 'default' && row.defaultValue !== undefined && (
              <span className="truncate font-mono text-[10px] text-slate-400">
                默认 {row.sensitive ? '••••••••' : row.defaultValue}
              </span>
            )}
          </div>
        </TableCell>

        {/* 操作 */}
        <TableCell className="w-25 pr-2">
          <div className="flex items-center gap-0.5">
            {canEdit && !isEditing && (
              <Button className="h-6 px-1.5 text-[10px] text-slate-400 hover:bg-accent-soft hover:text-accent-strong" onClick={() => startEdit(row)} title="编辑变量值" variant="ghost">
                <Pencil className="h-3 w-3" strokeWidth={1.5} />编辑
              </Button>
            )}
            {isEditing && (
              <Button className="h-6 px-1.5 text-[10px] text-slate-400 hover:bg-slate-100 hover:text-slate-700" onClick={cancelEdit} title="取消编辑 (Esc)" variant="ghost">
                <X className="h-3 w-3" strokeWidth={1.5} />
              </Button>
            )}
            <Button
              aria-pressed={watched}
              className={cn('h-6 px-1.5 text-[10px]', watched ? 'text-accent hover:text-slate-500' : 'text-slate-400 hover:text-accent')}
              onClick={() => toggleWatched(row.name)}
              title={watched ? '取消监视' : '监视此变量'}
              variant="ghost"
            >
              {watched ? <PinOff className="h-3 w-3" strokeWidth={1.5} /> : <Pin className="h-3 w-3" strokeWidth={1.5} />}
            </Button>
          </div>
        </TableCell>
      </TableRow>
    );
  };

  return (
    <TooltipProvider delayDuration={400}>
      <Table className="table-fixed min-w-145">
        <TableHeader className="sticky top-0 z-10 bg-white">
          <TableRow>
            <TableHead className="w-38 pl-2">变量名</TableHead>
            <TableHead className="w-17">类型</TableHead>
            <TableHead>当前值</TableHead>
            <TableHead className="w-15">作用域</TableHead>
            <TableHead className="w-39">来源</TableHead>
            <TableHead className="w-15 pr-2">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {flowRows.length > 0 && (
            <TableRow className="border-0 hover:bg-transparent">
              <TableCell className="bg-slate-50 py-1 pl-2" colSpan={6}>
                <div className="flex items-center gap-1.5">
                  <Workflow className="h-3 w-3 text-accent" strokeWidth={1.5} />
                  <span className="text-[10px] font-semibold text-slate-500">当前流程变量</span>
                </div>
              </TableCell>
            </TableRow>
          )}
          {flowRows.map((row) => renderRow(row, false))}

          {globalRows.length > 0 && (
            <TableRow className="border-0 hover:bg-transparent">
              <TableCell className="bg-accent-soft py-1 pl-2" colSpan={6}>
                <div className="flex items-center gap-1.5">
                  <Globe className="h-3 w-3 text-accent" strokeWidth={1.5} />
                  <span className="text-[10px] font-semibold text-accent-strong">全局变量</span>
                </div>
              </TableCell>
            </TableRow>
          )}
          {globalRows.map((row) => renderRow(row, true))}
        </TableBody>
      </Table>
    </TooltipProvider>
  );
}
