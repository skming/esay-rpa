import { Braces, Search, Variable } from 'lucide-react';
import type { ReactElement } from 'react';
import { useMemo, useState } from 'react';

import type { RuntimeVariable } from '../../../types/rpa';
import { IconButton } from '../../ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '../../ui/dropdown-menu';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { TypeBadge } from '../bottom-panel/TypeBadge';

const SCOPE_LABELS: RuntimeVariable['scope'][] = ['全局', '循环', '局部'];

function variableToken(name: string): string {
  return `\${var.${name}}`;
}

function scopeDotClass(scope: RuntimeVariable['scope']): string {
  if (scope === '循环') return 'bg-amber-400';
  if (scope === '局部') return 'bg-accent';
  return 'bg-slate-400';
}

export function VariablePickerField({
  label = '输入值',
  onChange,
  variables,
  value,
  placeholder = '输入文本或选择变量…',
}: {
  label?: string;
  onChange: (value: string) => void;
  variables?: RuntimeVariable[];
  value: string;
  placeholder?: string;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');

  const rows = variables ?? [];
  const normalizedQuery = query.trim().toLowerCase();

  const groupedRows = useMemo(
    () =>
      SCOPE_LABELS.map((scope) => ({
        rows: rows.filter((row) => {
          if (row.scope !== scope) return false;
          if (!normalizedQuery) return true;
          return (
            row.name.toLowerCase().includes(normalizedQuery) ||
            row.value.toLowerCase().includes(normalizedQuery) ||
            row.type.toLowerCase().includes(normalizedQuery)
          );
        }),
        scope,
      })).filter((g) => g.rows.length > 0),
    [normalizedQuery, rows]
  );

  const selectVariable = (name: string): void => {
    onChange(variableToken(name));
    setOpen(false);
    setQuery('');
  };

  return (
    <Label className="block">
      <span className="mb-1 block">{label}</span>
      <span className="relative block">
        <Input
          className="pr-8 font-mono text-[11px]"
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          tone="accent"
          value={value}
        />
        <DropdownMenu onOpenChange={(o) => { setOpen(o); if (!o) setQuery(''); }} open={open}>
          <DropdownMenuTrigger asChild>
            <IconButton
              className="absolute inset-y-1 right-1 h-6 w-6 text-accent-strong hover:bg-accent-soft"
              label="选择变量"
            >
              <Braces className="h-3.5 w-3.5" strokeWidth={1.5} />
            </IconButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-72 p-2">
            <div className="relative mb-2">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" strokeWidth={1.5} />
              <Input
                autoFocus
                className="h-8 pl-7 text-[11px]"
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索变量..."
                value={query}
              />
            </div>

            {groupedRows.length === 0 ? (
              <div className="grid place-items-center gap-1 rounded-md border border-dashed border-slate-200 bg-slate-50 px-3 py-5 text-center">
                <Variable className="h-6 w-6 text-slate-300" strokeWidth={1.5} />
                <div className="text-[11px] font-semibold text-slate-600">未找到变量</div>
                <div className="text-[10px] text-slate-400">在变量管理中添加后重试</div>
              </div>
            ) : (
              groupedRows.map((group, idx) => (
                <div key={group.scope}>
                  {idx > 0 && <DropdownMenuSeparator />}
                  <div className="px-2 py-1 text-[10px] font-bold uppercase tracking-normal text-slate-400">
                    {group.scope}变量
                  </div>
                  {group.rows.map((row) => (
                    <DropdownMenuItem
                      className="h-auto gap-2 px-2 py-1.5"
                      key={`${row.scope}-${row.name}`}
                      onSelect={() => selectVariable(row.name)}
                    >
                      <span className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${scopeDotClass(row.scope)}`} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-mono text-[11px] text-accent-strong">{row.name}</span>
                        <span className="block truncate font-mono text-[10px] text-slate-400" title={row.value}>
                          {row.sensitive ? '••••••••' : row.value}
                        </span>
                      </span>
                      <TypeBadge type={row.type} />
                    </DropdownMenuItem>
                  ))}
                </div>
              ))
            )}

            <div className="mt-2 rounded-md bg-accent-soft px-2 py-1 text-[10px] leading-4 text-accent-strong">
              选择后将覆盖当前内容。如需修改变量值，请前往变量管理。
            </div>
          </DropdownMenuContent>
        </DropdownMenu>
      </span>
    </Label>
  );
}
