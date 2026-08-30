import { Archive, FolderOpen, ListTodo, Loader2, Plus, RotateCcw, Trash2 } from 'lucide-react';
import { RunDetailDialog } from './RunDetailDialog';
import type { ReactElement } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';

import { FlowCard } from './FlowCard';
import { FlowCreateDialog } from './FlowCreateDialog';
import { FlowListTable } from './FlowListTable';
import { FlowListToolbar } from './FlowListToolbar';
import { RunHistoryDrawer } from './RunHistoryDrawer';
import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { buildFlowListItems, filterFlowItems, formatRelativeTime } from '../../lib/taskCenter';
import { cn } from '../../lib/utils';
import { useWorkspaceStore } from '../../stores/useWorkspaceStore';
import type { FlowSnapshot } from '../../types/electron';
import { Button } from '../ui/button';
import { RefreshButton } from '../ui/refresh-button';
import { Table, TableBody, TableCell, TableRow } from '../ui/table';
import { ScheduleCreateDialog } from '../studio/property-panel/ScheduleCreateDialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '../ui/alert-dialog';
import { EmptyPanel, Figure, StatBand, SURFACE } from './surfaces';
import { WorkspaceShell } from './WorkspaceShell';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../ui/dropdown-menu';
import { MoreHorizontal } from 'lucide-react';

export function TaskCenterPage({
  electron,
  onOpenStudio,
}: {
  electron: ElectronBridgeState;
  onOpenStudio: () => void;
}): ReactElement {
  const [createOpen, setCreateOpen] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [deleteFlowId, setDeleteFlowId] = useState<string | null>(null);
  const [historyFlowId, setHistoryFlowId] = useState<string | null>(null);
  const [scheduleFlowId, setScheduleFlowId] = useState<string | null>(null);
  const [detailRun, setDetailRun] = useState<import('../../types/electron').TaskSnapshot | null>(null);
  const loadedRef = useRef(false);

  const flowQuery = useWorkspaceStore((s) => s.flowQuery);
  const setFlowQuery = useWorkspaceStore((s) => s.setFlowQuery);
  const selectedFolder = useWorkspaceStore((s) => s.selectedFolder);
  const viewMode = useWorkspaceStore((s) => s.viewMode);
  const setViewMode = useWorkspaceStore((s) => s.setViewMode);

  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    void electron.loadFlows({ silent: true });
    void electron.loadSchedules({ silent: true });
    void electron.loadQueueStats({ silent: true });
  }, [electron]);

  const runningFlowId = electron.runtimeStatus === 'running' ? electron.activeRunFlowId ?? electron.currentFlow?.flowId ?? null : null;

  const items = useMemo(
    () => buildFlowListItems(electron.flows, electron.schedules, runningFlowId),
    [electron.flows, electron.schedules, runningFlowId],
  );

  const archivedFlows = useMemo(
    () => electron.flows
      .filter((f) => f.status === 'archived')
      .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()),
    [electron.flows],
  );

  const visibleItems = useMemo(
    () => filterFlowItems(items, flowQuery, selectedFolder),
    [flowQuery, items, selectedFolder],
  );

  const filteredArchivedFlows = useMemo(() => {
    const q = flowQuery.trim().toLowerCase();
    if (q === '') return archivedFlows;
    return archivedFlows.filter((f) =>
      `${f.name} ${f.version}`.toLowerCase().includes(q),
    );
  }, [archivedFlows, flowQuery]);

  const historyTarget = historyFlowId === null
    ? null
    : (items.find((i) => i.flow.flowId === historyFlowId) ?? null);

  const deleteTargetName = useMemo(() => {
    if (deleteFlowId === null) return '当前流程';
    return (
      items.find((i) => i.flow.flowId === deleteFlowId)?.flow.name ??
      archivedFlows.find((f) => f.flowId === deleteFlowId)?.name ??
      '当前流程'
    );
  }, [deleteFlowId, items, archivedFlows]);

  const openFlow = (flowId: string): void => { void electron.openFlowById(flowId).then(onOpenStudio); };
  const runFlow = (flowId: string): void => { void electron.startRun({ flowId, mode: 'run', scope: 'full' }); };
  const stopFlow = (): void => { void electron.stopRun(); };
  const archiveFlow = (flowId: string): void => { void electron.archiveFlowById(flowId); };
  const restoreFlow = (flowId: string): void => { void electron.setFlowStatusById(flowId, 'active'); };
  const setStatus = (flowId: string, status: import('../../types/electron').FlowStatus): void => {
    void electron.setFlowStatusById(flowId, status);
  };
  const openHistory = (flowId: string): void => {
    setHistoryFlowId(flowId);
    void electron.loadFlowRuns(flowId, { limit: 20, silent: true });
  };
  const exportFlow = (flowId: string): void => {
    void electron.exportFlowById(flowId);
  };
  const importFlow = (): void => {
    setIsImporting(true);
    void electron.openFlow().then((ok) => {
      setIsImporting(false);
      if (ok) onOpenStudio();
    });
  };

  return (
    <WorkspaceShell
      actions={
        <>
          <RefreshButton
            variant="subtle"
            onClick={() => { void electron.loadFlows(); void electron.loadSchedules(); }}
          >
            刷新
          </RefreshButton>
          <Button className="h-8 rounded-md px-3" disabled={isImporting} onClick={importFlow} variant="subtle">
            {isImporting
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.5} />
              : <FolderOpen className="h-3.5 w-3.5" strokeWidth={1.5} />}
            {isImporting ? '导入中…' : '导入流程'}
          </Button>
          <Button className="h-8 rounded-md px-3.5" disabled={isImporting} onClick={() => setCreateOpen(true)} variant="primary">
            <Plus className="h-3.5 w-3.5" strokeWidth={1.75} />
            新建流程
          </Button>
        </>
      }
      description="流程的运行、调度与历史"
      title="任务管理"
    >
      <StatBand>
        <Figure first label="活动流程" value={items.length} note="已配置" />
        <Figure label="已调度" value={items.filter((i) => i.nextRunAt !== null).length} note="自动触发" />
        <Figure
          label="运行中"
          value={electron.queueStats?.activeCount ?? 0}
          note="活跃"
          tone={(electron.queueStats?.activeCount ?? 0) > 0 ? 'live' : 'ink'}
        />
        <Figure
          label="最近更新"
          value={items[0] === undefined ? '--' : formatRelativeTime(items[0].flow.updatedAt)}
        />
      </StatBand>

      <FlowListToolbar
        archiveCount={archivedFlows.length}
        showArchived={showArchived}
        onQueryChange={setFlowQuery}
        onToggleArchived={() => { setShowArchived((v) => !v); setFlowQuery(''); }}
        onViewModeChange={setViewMode}
        query={flowQuery}
        viewMode={viewMode}
      />

      <div className="min-w-0">
        {showArchived ? (
          <ArchivedList
            flows={filteredArchivedFlows}
            onDelete={setDeleteFlowId}
            onRestore={restoreFlow}
          />
        ) : visibleItems.length === 0 ? (
          <EmptyActiveState />
        ) : viewMode === 'card' ? (
          <div className="grid gap-4 md:grid-cols-3 xl:grid-cols-4">
            {visibleItems.map((item) => (
              <FlowCard
                item={item}
                key={item.flow.flowId}
                onArchive={archiveFlow}
                onDelete={setDeleteFlowId}
                onEdit={openFlow}
                onExport={exportFlow}
                onHistory={openHistory}
                onRun={runFlow}
                onSchedule={setScheduleFlowId}
                onSetStatus={setStatus}
                onStop={stopFlow}
              />
            ))}
          </div>
        ) : (
          <FlowListTable
            items={visibleItems}
            onArchive={archiveFlow}
            onDelete={setDeleteFlowId}
            onEdit={openFlow}
            onExport={exportFlow}
            onHistory={openHistory}
            onRun={runFlow}
            onSchedule={setScheduleFlowId}
            onSetStatus={setStatus}
            onStop={stopFlow}
          />
        )}
      </div>

      <FlowCreateDialog
        onCreate={(name) => {
          setCreateOpen(false);
          void electron.createNewFlow(name).then(onOpenStudio);
        }}
        onOpenChange={setCreateOpen}
        open={createOpen}
      />
      <ScheduleCreateDialog
        onCreate={(options) => {
          if (scheduleFlowId !== null) void electron.createScheduleForFlow(scheduleFlowId, options);
        }}
        onOpenChange={(open) => { if (!open) setScheduleFlowId(null); }}
        open={scheduleFlowId !== null}
      />
      <AlertDialog onOpenChange={(open) => !open && setDeleteFlowId(null)} open={deleteFlowId !== null}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除流程</AlertDialogTitle>
            <AlertDialogDescription>
              将删除「{deleteTargetName}」。此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deleteFlowId !== null) void electron.deleteFlowById(deleteFlowId);
                setDeleteFlowId(null);
              }}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <RunDetailDialog
        onOpenArtifact={(artifact) => void electron.openArtifactPath(artifact.storageUrl)}
        onOpenChange={(open) => { if (!open) setDetailRun(null); }}
        open={detailRun !== null}
        run={detailRun}
      />
      <RunHistoryDrawer
        flowName={historyTarget?.flow.name ?? ''}
        onClose={() => setHistoryFlowId(null)}
        onInspectRun={(run) => {
          void electron.loadTaskVariables(run.taskId);
          void electron.loadArtifacts(run.taskId);
          setDetailRun(run);
        }}
        onRefresh={() => { if (historyFlowId !== null) void electron.loadFlowRuns(historyFlowId, { limit: 20 }); }}
        open={historyFlowId !== null}
        runs={electron.runs}
      />
    </WorkspaceShell>
  );
}

function EmptyActiveState(): ReactElement {
  return (
    <EmptyPanel
      icon={<ListTodo className="h-6 w-6" strokeWidth={1.25} />}
      title="暂无匹配流程"
      hint="点击上方「新建流程」开始创建第一个自动化任务，或导入已有的 .rpa.json。"
    />
  );
}

function ArchivedList({
  flows,
  onDelete,
  onRestore,
}: {
  flows: FlowSnapshot[];
  onDelete: (flowId: string) => void;
  onRestore: (flowId: string) => void;
}): ReactElement {
  if (flows.length === 0) {
    return (
      <EmptyPanel
        icon={<Archive className="h-6 w-6" strokeWidth={1.25} />}
        title="暂无归档流程"
        hint="将不再需要的流程归档后，可在此处恢复或彻底删除。"
      />
    );
  }
  return (
    <div className={cn('overflow-hidden', SURFACE)}>
      <Table>
        <TableBody>
          {flows.map((flow) => (
            <TableRow className="border-rule hover:bg-paper" key={flow.flowId}>
              <TableCell className="pl-5">
                <div className="text-[12.5px] font-medium text-ink-2">{flow.name}</div>
                <div className="mt-0.5 font-mono text-[10px] tabular-nums text-ink-3">
                  {flow.version} · 归档于 {formatRelativeTime(flow.updatedAt)}
                </div>
              </TableCell>
              <TableCell className="pr-5 flex justify-end">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button className="h-7 rounded-md px-2.5 text-[11px]" variant="ghost">
                      <MoreHorizontal className="h-3 w-3" strokeWidth={1.5} />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onClick={() => onRestore(flow.flowId)}>
                      <RotateCcw className="mr-2 h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} />
                      恢复
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => onDelete(flow.flowId)}>
                      <Trash2 className="mr-2 h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} />
                      删除
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
