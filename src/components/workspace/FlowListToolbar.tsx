import { Grid2X2, List } from 'lucide-react';
import type { ReactElement } from 'react';

import type { FlowListViewMode } from '../../stores/useWorkspaceStore';
import { cn } from '../../lib/utils';
import { IconButton } from '../ui/button';
import { SearchField } from './surfaces';

// 只管筛选与视图，新建/导入/刷新在页头 actions
export function FlowListToolbar({
  archiveCount,
  showArchived,
  onToggleArchived,
  onViewModeChange,
  query,
  viewMode,
  onQueryChange,
}: {
  archiveCount: number;
  showArchived: boolean;
  onToggleArchived: () => void;
  onViewModeChange: (mode: FlowListViewMode) => void;
  query: string;
  viewMode: FlowListViewMode;
  onQueryChange: (query: string) => void;
}): ReactElement {
  return (
    <div className="flex items-center gap-3">
      <div className="flex shrink-0 items-center gap-0.5 rounded-md border border-rule-2 bg-paper-sunk p-0.5">
        <SegmentButton active={!showArchived} onClick={() => showArchived && onToggleArchived()}>
          活动流程
        </SegmentButton>
        <SegmentButton active={showArchived} onClick={() => !showArchived && onToggleArchived()}>
          已归档
          {archiveCount > 0 && (
            <span
              className={cn(
                'rounded-full px-1.5 py-0.5 font-mono text-[10px] leading-none tabular-nums',
                showArchived ? 'bg-accent-soft text-accent-strong' : 'bg-rule-2 text-ink-2',
              )}
            >
              {archiveCount}
            </span>
          )}
        </SegmentButton>
      </div>

      <SearchField
        label="搜索流程"
        onChange={onQueryChange}
        placeholder={showArchived ? '搜索归档流程' : '搜索流程、版本或目录'}
        value={query}
      />

      {!showArchived && (
        <div className="flex shrink-0 items-center gap-0.5 rounded-md border border-rule-2 bg-paper-sunk p-0.5">
          <IconButton active={viewMode === 'card'} className="h-7 w-7" label="卡片视图" onClick={() => onViewModeChange('card')}>
            <Grid2X2 className="h-3.5 w-3.5" strokeWidth={1.5} />
          </IconButton>
          <IconButton active={viewMode === 'list'} className="h-7 w-7" label="列表视图" onClick={() => onViewModeChange('list')}>
            <List className="h-3.5 w-3.5" strokeWidth={1.5} />
          </IconButton>
        </div>
      )}
    </div>
  );
}

function SegmentButton({
  active, children, onClick,
}: { active: boolean; children: ReactElement | (ReactElement | string | false)[] | string; onClick: () => void }): ReactElement {
  return (
    <button
      aria-pressed={active}
      className={cn(
        'flex h-7 items-center gap-1.5 rounded px-2.5 text-[12px] font-medium transition-colors duration-150',
        active ? 'bg-surface text-ink shadow-xs' : 'text-ink-2 hover:text-ink',
      )}
      onClick={onClick}
      type="button"
    >
      {children}
    </button>
  );
}
