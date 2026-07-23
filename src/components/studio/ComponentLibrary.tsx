import { ChevronDown, ChevronRight, GripVertical, PanelLeftClose, PanelLeftOpen, Search } from 'lucide-react';
import type { DragEvent, ReactElement } from 'react';
import { useMemo, useState } from 'react';

import { componentGroups, kindStyles, totalComponents } from '../../data/studioData';
import type { ComponentItem, NodeKind } from '../../types/rpa';
import type { ComponentDragPayload } from '../../lib/flowOperations';
import { Badge } from '../ui/badge';
import { IconButton } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { cn } from '../../lib/utils';

export function ComponentLibrary({
  onQuickAdd,
}: {
  onQuickAdd: (payload: ComponentDragPayload) => void;
}): ReactElement {
  const [collapsed, setCollapsed] = useState(false);
  const [query, setQuery] = useState('');
  const [expanded, setExpanded] = useState<Record<NodeKind, boolean>>({
    browser: true, excel: false, ui: false, file: false,
    data: false, script: false, control: false, variable: false,
  });

  const normalizedQuery = query.trim();

  const filteredGroups = useMemo(
    () =>
      componentGroups
        .map((g) => ({
          ...g,
          items: normalizedQuery.length === 0
            ? g.items
            : g.items.filter((i) => i.label.includes(normalizedQuery)),
        }))
        .filter((g) => normalizedQuery.length === 0 || g.items.length > 0),
    [normalizedQuery],
  );

  return (
    <aside
      className={cn(
        'flex shrink-0 flex-col border-r border-slate-200/70 bg-white text-[11px]',
        'overflow-hidden transition-[width] duration-200 ease-in-out',
        collapsed ? 'w-10' : 'w-56',
      )}
    >
      {/* Header — 折叠按钮放最左保证 w-10 时仍可见 */}
      <div className="flex h-10 shrink-0 items-center gap-1.5 border-b border-slate-100 px-1.5">
        <IconButton
          label={collapsed ? '展开组件库' : '折叠组件库'}
          onClick={() => setCollapsed(!collapsed)}
        >
          {collapsed
            ? <PanelLeftOpen className="h-3.5 w-3.5" strokeWidth={1.5} />
            : <PanelLeftClose className="h-3.5 w-3.5" strokeWidth={1.5} />}
        </IconButton>
        <span
          className={cn(
            'min-w-0 flex-1 truncate text-[11px] font-semibold text-slate-700',
            'transition-opacity duration-150',
            collapsed ? 'opacity-0' : 'opacity-100',
          )}
        >
          组件库
        </span>
      </div>

      <div
        className={cn(
          'min-h-0 flex-1 overflow-y-auto',
          'transition-opacity duration-150',
          collapsed ? 'pointer-events-none opacity-0' : 'opacity-100',
        )}
      >
        <div className="px-2.5 pt-2.5 pb-1.5">
          <Label
            className={cn(
              'flex h-7 items-center gap-2 rounded-lg border px-2.5 text-slate-500 transition-all duration-150',
              'border-slate-200 bg-slate-50/60',
              'focus-within:border-accent-line focus-within:bg-white focus-within:ring-3 focus-within:ring-accent-soft',
            )}
          >
            <Search className="h-3 w-3 shrink-0" strokeWidth={1.5} />
            <Input
              className="h-auto min-w-0 flex-1 rounded-none border-0 bg-transparent px-0 text-[11px] text-slate-700 placeholder:text-slate-500 focus:ring-0 focus:outline-hidden"
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索组件…"
              value={query}
            />
          </Label>
        </div>

        <div className="px-2 pb-3">
          {filteredGroups.map((group) => {
            const style = kindStyles[group.id];
            const Icon = group.icon;
            const isExpanded = normalizedQuery.length > 0 || expanded[group.id];

            return (
              <section className="mb-0.5" key={group.id}>
                <button
                  className="flex h-8 w-full items-center justify-start gap-2 rounded-lg px-2 text-left text-[11px] font-semibold text-slate-700 transition-colors duration-150 hover:bg-slate-50"
                  onClick={() => setExpanded((cur) => ({ ...cur, [group.id]: !cur[group.id] }))}
                  type="button"
                >
                  <span
                    className="grid h-4.5 w-4.5 shrink-0 place-items-center rounded-md"
                    style={{ background: style.bg, color: style.accent }}
                  >
                    <Icon className="h-3 w-3" strokeWidth={1.5} />
                  </span>
                  <span className="min-w-0 flex-1 truncate">{group.label}</span>
                  <Badge style={{ color: style.accent, backgroundColor: style.bg }}>{group.items.length}</Badge>
                  {isExpanded
                    ? <ChevronDown className="h-3 w-3 text-slate-400" strokeWidth={1.5} />
                    : <ChevronRight className="h-3 w-3 text-slate-400" strokeWidth={1.5} />}
                </button>
                {isExpanded && (
                  <div className="mt-0.5 space-y-px pl-7 pr-1">
                    {group.items.map((item) => (
                      <ComponentLibraryItem group={group.id} item={item} key={item.label} onQuickAdd={onQuickAdd} />
                    ))}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      </div>

      <div
        className={cn(
          'flex h-9 shrink-0 items-center border-t border-slate-100 px-3 text-[10px] text-slate-500',
          'transition-opacity duration-150',
          collapsed ? 'pointer-events-none opacity-0' : 'opacity-100',
        )}
      >
        共 {totalComponents} 个组件
      </div>
    </aside>
  );
}

function ComponentLibraryItem({
  group, item, onQuickAdd,
}: {
  group: NodeKind;
  item: ComponentItem;
  onQuickAdd: (payload: ComponentDragPayload) => void;
}): ReactElement {
  const style = kindStyles[group];
  const payload: ComponentDragPayload = { nodeType: group, label: item.label };

  const handleDragStart = (e: DragEvent<HTMLButtonElement>): void => {
    e.dataTransfer.effectAllowed = 'copy';
    e.dataTransfer.setData('application/rpa-node', JSON.stringify(payload));
  };

  return (
    <button
      className={cn(
        'group flex h-8 w-full cursor-grab items-center justify-start gap-2 rounded-lg px-2 text-left text-[11px] text-slate-600',
        'transition-all duration-150 hover:bg-accent-soft hover:text-accent-strong',
        'active:cursor-grabbing active:scale-[0.98]',
      )}
      draggable
      onClick={() => onQuickAdd(payload)}
      onDragStart={handleDragStart}
      type="button"
    >
      <span className="h-1.5 w-1.5 shrink-0 rounded-full opacity-70" style={{ background: style.accent }} />
      <span className="min-w-0 flex-1 truncate">{item.label}</span>
      {item.popular && (
        <Badge className="rounded-md px-1 text-[9px] border-amber-100 bg-amber-50 text-amber-600" variant="amber">
          常用
        </Badge>
      )}
      <GripVertical className="hidden h-3 w-3 shrink-0 text-accent opacity-70 group-hover:block" strokeWidth={1.5} />
    </button>
  );
}
