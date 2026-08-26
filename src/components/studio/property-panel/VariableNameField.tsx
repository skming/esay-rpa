import { Search, Variable } from 'lucide-react';
import type { ChangeEvent, ReactElement } from 'react';
import { useMemo, useState } from 'react';

import { validateVariableNameInput } from '../../../lib/variableNaming';
import { cn } from '../../../lib/utils';
import type { RuntimeVariable } from '../../../types/rpa';
import { IconButton } from '../../ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '../../ui/dropdown-menu';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { TypeBadge } from '../bottom-panel/TypeBadge';

const SCOPE_LABELS: RuntimeVariable['scope'][] = ['全局', '循环', '局部'];

// reference：引用已存在变量（校验要求命中 existingNames）；target：填新的输出变量名（校验规则相反，重名会警告）
type VariableNameFieldMode = 'reference' | 'target';

export function VariableNameField({
  label,
  mode = 'reference',
  onChange,
  placeholder,
  value,
  variables
}: {
  label: string;
  mode?: VariableNameFieldMode;
  onChange: (value: string) => void;
  placeholder?: string;
  value: string;
  variables?: RuntimeVariable[];
}): ReactElement {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  // 走 useMemo 而不是裸 `variables ?? []`：variables 缺省时每次渲染都是新数组，
  // 下面两个 useMemo 的依赖恒变，等于没缓存。
  const rows = useMemo(() => variables ?? [], [variables]);
  const existingNames = useMemo(() => rows.map((row) => row.name), [rows]);
  const normalizedQuery = query.trim().toLowerCase();
  const validation = useMemo(() => validateVariableNameInput(value, { existingNames, mode }), [existingNames, mode, value]);
  const groupedRows = useMemo(
    () =>
      SCOPE_LABELS.map((scope) => ({
        rows: rows.filter((row) => {
          if (row.scope !== scope) {
            return false;
          }
          if (normalizedQuery.length === 0) {
            return true;
          }
          return row.name.toLowerCase().includes(normalizedQuery) || row.value.toLowerCase().includes(normalizedQuery) || row.type.toLowerCase().includes(normalizedQuery);
        }),
        scope
      })).filter((group) => group.rows.length > 0),
    [normalizedQuery, rows]
  );

  return (
    <Label className="block">
      <span className="mb-1 block">{label}</span>
      <span className="relative block">
        <Input
          aria-invalid={validation.issue === 'invalid'}
          className={cn(
            'pr-8 font-mono text-[11px]',
            validation.issue === 'invalid' && 'border-red-200 bg-red-50 text-red-700 focus:border-red-300 focus:ring-red-100',
            validation.issue !== 'invalid' && validation.issue !== null && 'border-amber-200 bg-amber-50 text-amber-800 focus:border-amber-300 focus:ring-amber-100'
          )}
          onChange={(event: ChangeEvent<HTMLInputElement>) => onChange(event.target.value)}
          placeholder={placeholder}
          tone={mode === 'target' ? 'blue' : 'accent'}
          value={value}
        />
        <DropdownMenu onOpenChange={setOpen} open={open}>
          <DropdownMenuTrigger asChild>
            <IconButton className="absolute inset-y-1 right-1 h-6 w-6 text-accent-strong hover:bg-accent-soft" label={`打开${label}选择器`}>
              <Variable className="h-3.5 w-3.5" strokeWidth={1.5} />
            </IconButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-72 p-2">
            <div className="relative mb-2">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" strokeWidth={1.5} />
              <Input
                autoFocus
                className="h-8 pl-7 text-[11px]"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索变量..."
                value={query}
              />
            </div>
            {groupedRows.length === 0 ? (
              <div className="grid place-items-center gap-1 rounded-md border border-dashed border-slate-200 bg-slate-50 px-3 py-5 text-center">
                <Variable className="h-6 w-6 text-slate-300" strokeWidth={1.5} />
                <div className="text-[11px] font-semibold text-slate-600">未找到变量</div>
                <div className="text-[10px] text-slate-500">{mode === 'reference' ? '可继续手动输入变量名' : '可继续输入新的输出变量名'}</div>
              </div>
            ) :
              <div className='max-h-72 overflow-y-auto'>
                {
                  groupedRows.map((group, groupIndex) => (
                    <div key={group.scope}>
                      {groupIndex > 0 && <DropdownMenuSeparator />}
                      <div className="px-2 py-1 text-[10px] font-bold uppercase tracking-normal text-slate-500">{group.scope}变量</div>
                      {group.rows.map((row) => (
                        <DropdownMenuItem className="h-auto gap-2 px-2 py-1.5" key={`${group.scope}-${row.name}`} onSelect={() => {
                          onChange(row.name);
                          setOpen(false);
                        }}>
                          <span className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${scopeDotClass(row.scope)}`} />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate font-mono text-[11px] text-blue-700">{row.name}</span>
                            <span className="block truncate font-mono text-[10px] text-slate-500" title={row.value}>
                              {row.value}
                            </span>
                          </span>
                          <TypeBadge type={row.type} />
                        </DropdownMenuItem>
                      ))}
                    </div>
                  ))}
              </div>
            }
            <div className="mt-2 rounded-md bg-accent-soft px-2 py-1 text-[10px] leading-4 text-accent-strong">
              {mode === 'reference' ? '选择已有变量名，节点会直接引用该变量。' : '可选择已有变量进行覆盖，也可输入新的输出变量名。'}
            </div>
          </DropdownMenuContent>
        </DropdownMenu>
      </span>
      {validation.message !== null && (
        <span className={cn('mt-1 block text-[10px] leading-4', validation.issue === 'invalid' ? 'text-red-600' : 'text-amber-700')}>
          {validation.message}
        </span>
      )}
    </Label>
  );
}

function scopeDotClass(scope: RuntimeVariable['scope']): string {
  if (scope === '循环') {
    return 'bg-amber-400';
  }
  if (scope === '局部') {
    return 'bg-blue-500';
  }
  return 'bg-slate-400';
}
