import { FolderOpen, Grid2X2, List, Loader2, Plus, Search } from 'lucide-react';
import type { ReactElement } from 'react';

import type { FlowListViewMode } from '../../stores/useWorkspaceStore';
import { cn } from '../../lib/utils';
import { Button, IconButton } from '../ui/button';
import { Input } from '../ui/input';
import { RefreshButton } from '../ui/refresh-button';

export function FlowListToolbar({
  archiveCount,
  disabled,
  showArchived,
  onCreate,
  onImport,
  onRefresh,
  onToggleArchived,
  onViewModeChange,
  query,
  viewMode,
  onQueryChange
}: {
  archiveCount: number;
  disabled?: boolean;
  showArchived: boolean;
  onCreate: () => void;
  onImport: () => void;
  onRefresh: () => void;
  onToggleArchived: () => void;
  onViewModeChange: (mode: FlowListViewMode) => void;
  query: string;
  viewMode: FlowListViewMode;
  onQueryChange: (query: string) => void;
}): ReactElement {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-rule bg-surface p-2">
      {/* 活动 / 已归档 分段 */}
      <div className="flex items-center gap-0.5 rounded-md border border-rule-2 bg-paper-sunk p-0.5 shrink-0">
        <button
          className={cn(
            'flex h-7 items-center rounded px-2.5 text-[12px] font-medium transition-colors',
            !showArchived
              ? 'bg-surface text-ink shadow-sm'
              : 'text-ink-3 hover:text-ink-2',
          )}
          onClick={() => showArchived && onToggleArchived()}
          type="button"
        >
          活动流程
        </button>
        <button
          className={cn(
            'flex h-7 items-center gap-1.5 rounded px-2.5 text-[12px] font-medium transition-colors',
            showArchived
              ? 'bg-surface text-ink shadow-sm'
              : 'text-ink-3 hover:text-ink-2',
          )}
          onClick={() => !showArchived && onToggleArchived()}
          type="button"
        >
          已归档
          {archiveCount > 0 && (
            <span className={cn(
              'rounded-full px-1.5 py-0.5 font-mono text-[10px] tabular-nums leading-none',
              showArchived ? 'bg-accent-soft text-accent-strong' : 'bg-rule-2 text-ink-3',
            )}>
              {archiveCount}
            </span>
          )}
        </button>
      </div>

      {/* 搜索 */}
      <div className="relative min-w-52 flex-1">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-4" strokeWidth={1.5} />
        <Input
          className="h-8 rounded-md border-rule-2 bg-surface pl-8 text-[12px] text-ink-2 placeholder:text-ink-4 focus:border-accent focus:ring-2 focus:ring-accent-soft"
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder={showArchived ? '搜索归档流程' : '搜索流程、版本或目录'}
          value={query}
        />
      </div>

      {!showArchived && (
        <>
          <div className="flex items-center gap-0.5 rounded-md border border-rule-2 bg-paper-sunk p-0.5">
            <IconButton active={viewMode === 'card'} className="h-7 w-7" label="卡片视图" onClick={() => onViewModeChange('card')}>
              <Grid2X2 className="h-3.5 w-3.5" strokeWidth={1.5} />
            </IconButton>
            <IconButton active={viewMode === 'list'} className="h-7 w-7" label="列表视图" onClick={() => onViewModeChange('list')}>
              <List className="h-3.5 w-3.5" strokeWidth={1.5} />
            </IconButton>
          </div>
          <RefreshButton variant="ledger" onClick={onRefresh}>刷新</RefreshButton>
          <Button disabled={disabled} onClick={onImport} variant="ledger" className="h-8 rounded-md px-3">
            {disabled ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.5} />
            ) : (
              <FolderOpen className="h-3.5 w-3.5" strokeWidth={1.5} />
            )}
            {disabled ? '导入中...' : '导入流程'}
          </Button>
          <Button disabled={disabled} onClick={onCreate} variant="primary" className="h-8 rounded-md px-3.5">
            <Plus className="h-3.5 w-3.5" strokeWidth={1.75} />
            新建流程
          </Button>
        </>
      )}

      {showArchived && (
        <RefreshButton variant="ledger" onClick={onRefresh}>刷新</RefreshButton>
      )}
    </div>
  );
}
