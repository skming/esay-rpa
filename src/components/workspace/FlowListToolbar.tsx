import type { ReactElement } from 'react';

import type { FlowFilter } from '../../lib/taskCenter';
import { cn } from '../../lib/utils';
import { SearchField } from './surfaces';

export type TaskCenterView = FlowFilter | 'archived';

const FILTERS: Array<{ label: string; value: TaskCenterView }> = [
  { label: '全部', value: 'all' },
  { label: '运行中', value: 'running' },
  { label: '失败', value: 'failed' },
  { label: '已调度', value: 'scheduled' },
  { label: '暂停', value: 'paused' },
  { label: '禁用', value: 'disabled' },
  { label: '归档', value: 'archived' },
];

export function FlowListToolbar({
  counts,
  onQueryChange,
  onViewChange,
  query,
  view,
}: {
  counts: Record<TaskCenterView, number>;
  onQueryChange: (query: string) => void;
  onViewChange: (view: TaskCenterView) => void;
  query: string;
  view: TaskCenterView;
}): ReactElement {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-rule bg-surface p-2 shadow-xs">
      <div className="no-scrollbar flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
        {FILTERS.map((item) => (
          <button
            aria-pressed={view === item.value}
            className={cn(
              'flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-[11px] font-medium transition-colors',
              view === item.value
                ? 'bg-accent-soft text-accent-strong'
                : 'text-ink-3 hover:bg-paper-sunk hover:text-ink-2',
            )}
            key={item.value}
            onClick={() => onViewChange(item.value)}
            type="button"
          >
            {item.label}
            <span className={cn(
              'font-mono text-[10px] tabular-nums',
              view === item.value ? 'text-accent-strong' : 'text-ink-4',
            )}>
              {counts[item.value]}
            </span>
          </button>
        ))}
      </div>
      <SearchField
        className="w-64 flex-none"
        label="搜索流程"
        onChange={onQueryChange}
        placeholder={view === 'archived' ? '搜索归档流程' : '搜索名称、版本或目录'}
        value={query}
      />
    </div>
  );
}
