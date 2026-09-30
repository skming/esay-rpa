import type { ReactElement } from 'react';

import type { FlowFilter } from '../../lib/taskCenter';
import { FilterBar } from './surfaces';

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
    <FilterBar
      counts={counts}
      filters={FILTERS}
      onQueryChange={onQueryChange}
      onValueChange={onViewChange}
      query={query}
      searchLabel="搜索流程"
      searchPlaceholder={view === 'archived' ? '搜索归档流程' : '搜索名称、版本或目录'}
      value={view}
    />
  );
}
