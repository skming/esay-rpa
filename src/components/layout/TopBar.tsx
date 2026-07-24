import {
  Archive, BookOpen, Check, ChevronDown, ChevronRight,
  Download, FolderTree, Hash, History, Loader2, Pencil,
  CirclePause, Play, Save, Square, Trash2, Variable, Workflow, XCircle,
  CheckCircle2, Bug,
} from 'lucide-react';
import type { KeyboardEvent, ReactElement } from 'react';
import { useRef, useState } from 'react';

import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { cn } from '../../lib/utils';
import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import type { FlowDraftAutosaveState } from '../../hooks/useFlowDraftAutosave';
import { useFlowVariableStore } from '../../stores/useFlowVariableStore';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from '../ui/dropdown-menu';
import { FlowVariablesDialog } from './FlowVariablesDialog';
import { RunConfigDialog } from './RunConfigDialog';
import { VersionHistoryDialog } from './VersionHistoryDialog';
import type { RpaNodeAction } from '../../types/rpa';

export function TopBar({
  visible = true,
  draftAutosave,
  electron,
  selectedNodeAction,
  selectedNodeId,
  selectedNodeTitle,
}: {
  visible?: boolean;
  draftAutosave: FlowDraftAutosaveState;
  electron: ElectronBridgeState;
  selectedNodeAction?: RpaNodeAction;
  selectedNodeId: string;
  selectedNodeTitle: string;
}): ReactElement {
  const [runConfigOpen, setRunConfigOpen] = useState(false);
  const [versionHistoryOpen, setVersionHistoryOpen] = useState(false);
  const [flowVariablesOpen, setFlowVariablesOpen] = useState(false);

  const inputVariables = useFlowVariableStore((s) => s.inputVariables);
  const addInputVariable = useFlowVariableStore((s) => s.addInputVariable);
  const removeInputVariable = useFlowVariableStore((s) => s.removeInputVariable);
  const updateInputVariable = useFlowVariableStore((s) => s.updateInputVariable);

  const running = electron.runtimeStatus === 'running';
  const activeFlowName = electron.currentFlow?.name ?? '未命名流程';
  const activeFlowId = electron.currentFlow?.flowId;
  const activeFlowVersion = electron.currentFlow?.version ?? '草稿';
  const activeFlowStatus = electron.currentFlow?.status ?? 'draft';

  if (!visible) return <></>;

  return (
    <header className="flex h-12 shrink-0 items-center overflow-hidden border-b border-slate-200/60 bg-white/97 px-4 backdrop-blur-sm shadow-[0_1px_3px_rgba(15,23,42,0.04)]">

      <div className="flex min-w-0 shrink-0 items-center gap-1.5 text-[11px] text-slate-500">
        <FolderTree className="h-3.5 w-3.5 shrink-0" strokeWidth={1.5} />
        <span className="whitespace-nowrap">任务管理</span>
        <ChevronRight className="h-3 w-3 shrink-0 text-slate-300" strokeWidth={1.5} />
        <FlowNameEditor
          name={activeFlowName}
          onRename={(name) => void electron.renameCurrentFlow(name)}
        />
      </div>

      <div className="mx-3 h-4 w-px shrink-0 bg-slate-200" />

      <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-hidden">
        <FlowVersionMenu
          activeFlowId={activeFlowId}
          activeStatus={activeFlowStatus}
          label={activeFlowName}
          dirty={draftAutosave.dirty}
          lastAutosavedAt={draftAutosave.lastAutosavedAt}
          onArchiveCurrent={electron.archiveCurrentFlow}
          onDeleteCurrent={electron.deleteCurrentFlow}
          onExport={electron.exportFlow}
          onLoadFlows={electron.loadFlows}
          onOpenPicker={electron.openFlow}
          onOpenVersionHistory={() => { setVersionHistoryOpen(true); void electron.loadFlows(); }}
          onSave={electron.saveFlow}
          version={activeFlowVersion}
        />
        <Button
          className="h-7 text-slate-600 hover:border-slate-300 hover:text-slate-800"
          onClick={() => setFlowVariablesOpen(true)}
          variant="outline"
        >
          <Variable className="h-3.5 w-3.5 text-accent" strokeWidth={1.5} />
          变量管理
          {/* 直接标出已定义几个变量，省去"点进去才知道是不是空的"这一步 */}
          {inputVariables.length > 0 && (
            <Badge className="border-slate-200 bg-slate-50 font-mono text-slate-600 text-[9px]">
              {inputVariables.length}
            </Badge>
          )}
        </Button>
      </div>

      <div className="flex shrink-0 items-center gap-2">
        {/* ready 是"还没跑过"的默认态，与旁边启用中的「运行」按钮同义，不必再挂标签 */}
        {electron.runtimeStatus !== 'ready' && (
          <>
            <StatusPill electron={electron} />
            <div className="mx-1 h-4 w-px shrink-0 bg-slate-200" />
          </>
        )}

        <Button
          className="h-7 text-slate-600 hover:border-slate-300"
          disabled={running}
          onClick={() => void electron.startRun('debug')}
          variant="outline"
        >
          <Bug className="h-3.5 w-3.5" strokeWidth={1.5} />
          调试
        </Button>

        {running ? (
          <Button className="h-7" onClick={() => void electron.stopRun()} variant="danger">
            <Square className="h-3.5 w-3.5 fill-current" strokeWidth={1.5} />
            停止
          </Button>
        ) : (
          <div className="flex overflow-hidden rounded-lg shadow-sm">
            <Button
              className="h-7 rounded-r-none shadow-none"
              onClick={() => void electron.startRun('run')}
              variant="primary"
            >
              <Play className="h-3.5 w-3.5 fill-current" strokeWidth={1.5} />
              运行
            </Button>
            <Button
              aria-label="打开运行配置"
              className="h-7 w-7 rounded-l-none border-l border-white/20 px-0 shadow-none"
              onClick={() => setRunConfigOpen(true)}
              variant="primary"
            >
              <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.5} />
            </Button>
          </div>
        )}
      </div>

      <RunConfigDialog
        defaultBrowserExecutor={electron.currentFlow?.defaultBrowserExecutor}
        inputVariables={electron.inputVariables}
        onOpenChange={setRunConfigOpen}
        onSetDefaultBrowserExecutor={(browserExecutor) => void electron.setDefaultBrowserExecutor(browserExecutor)}
        onStart={(options) => void electron.startRun(options)}
        open={runConfigOpen}
        running={running}
        selectedNodeAction={selectedNodeAction}
        selectedNodeId={selectedNodeId}
        selectedNodeTitle={selectedNodeTitle}
      />
      <FlowVariablesDialog
        onAdd={addInputVariable}
        onOpenChange={(open) => {
          setFlowVariablesOpen(open);
          if (!open && electron.currentFlow) void electron.saveFlow();
        }}
        onRemove={removeInputVariable}
        onUpdate={updateInputVariable}
        open={flowVariablesOpen}
        variables={inputVariables}
      />
      <VersionHistoryDialog
        currentFlow={electron.currentFlow}
        onOpenChange={setVersionHistoryOpen}
        onRestoreSnapshot={(savedAt) => electron.rollbackFlowById(savedAt).then(() => setVersionHistoryOpen(false))}
        open={versionHistoryOpen}
      />
    </header>
  );
}

function FlowNameEditor({ name, onRename }: { name: string; onRename: (name: string) => void }): ReactElement {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  function startEdit() {
    setDraft(name);
    setEditing(true);
    setTimeout(() => { inputRef.current?.select(); }, 0);
  }

  function commit() {
    const trimmed = draft.trim();
    if (trimmed && trimmed !== name) onRename(trimmed);
    setEditing(false);
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') setEditing(false);
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        autoFocus
        className="h-6 max-w-48 rounded border border-accent px-1.5 text-[11px] font-semibold text-slate-700 outline-none ring-2 ring-accent/20"
        onBlur={commit}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        value={draft}
      />
    );
  }

  return (
    <button
      className="group flex max-w-72 items-center gap-1 rounded px-1 py-0.5 text-[11px] font-semibold text-slate-700 hover:bg-slate-100"
      onClick={startEdit}
      title="点击重命名"
      type="button"
    >
      <span className="truncate">{name}</span>
      <Pencil className="h-2.5 w-2.5 shrink-0 text-slate-300 opacity-0 transition-opacity group-hover:opacity-100" strokeWidth={2} />
    </button>
  );
}

function FlowIdCopy({ flowId }: { flowId: string }): ReactElement {
  const [copied, setCopied] = useState(false);

  function copy() {
    void navigator.clipboard.writeText(flowId).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <button
      className={cn(
        'flex h-5 items-center gap-1 rounded px-1.5 font-mono text-[9.5px] transition-colors',
        copied
          ? 'bg-emerald-50 text-emerald-600'
          : 'bg-slate-100 text-slate-500 hover:bg-slate-200 hover:text-slate-600',
      )}
      onClick={copy}
      type="button"
    >
      {copied
        ? <Check className="h-2.5 w-2.5 shrink-0" strokeWidth={2.5} />
        : <Hash className="h-2.5 w-2.5 shrink-0" strokeWidth={2} />
      }
      <span className="truncate">{flowId}</span>
    </button>
  );
}

function StatusPill({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const { runtimeStatus } = electron;

  const cfg: Record<typeof runtimeStatus, { icon: ReactElement; label: string; cls: string }> = {
    ready: { icon: <CheckCircle2 className="h-3 w-3" strokeWidth={1.5} />, label: '待运行', cls: 'border-slate-200 bg-slate-50 text-slate-500' },
    running: { icon: <Loader2 className="h-3 w-3 animate-spin" strokeWidth={1.5} />, label: '运行中', cls: 'border-live-line bg-live-soft text-[#1d4ed8]' },
    success: { icon: <CheckCircle2 className="h-3 w-3" strokeWidth={1.5} />, label: '已完成', cls: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
    error: { icon: <XCircle className="h-3 w-3" strokeWidth={1.5} />, label: '运行失败', cls: 'border-red-200 bg-red-50 text-red-700' },
    stopped: { icon: <CirclePause className="h-3 w-3" strokeWidth={1.5} />, label: '已停止', cls: 'border-amber-200 bg-amber-50 text-amber-700' },
    paused_for_human: { icon: <Square className="h-3 w-3" strokeWidth={1.5} />, label: '等待接管', cls: 'border-amber-200 bg-amber-50 text-amber-700' },
  };

  const { icon, label, cls } = cfg[runtimeStatus];

  return (
    <div className={cn('flex h-6 items-center gap-1.5 rounded-full border px-2.5 text-[10px] font-medium', cls)}>
      {icon}
      <span>{label}</span>
      {runtimeStatus === 'running' && (
        <>
          <span className="h-3 w-px bg-current opacity-20" />
          <span className="font-mono">{electron.progress.percent}%</span>
        </>
      )}
    </div>
  );
}

function FlowVersionMenu({
  activeFlowId,
  activeStatus,
  label,
  dirty,
  lastAutosavedAt,
  onArchiveCurrent,
  onDeleteCurrent,
  onExport,
  onLoadFlows,
  onOpenPicker,
  onOpenVersionHistory,
  onSave,
  version,
}: {
  activeFlowId: string | undefined;
  activeStatus: string;
  label: string;
  dirty: boolean;
  lastAutosavedAt: string | null;
  onArchiveCurrent: () => Promise<void>;
  onDeleteCurrent: () => Promise<void>;
  onExport: () => Promise<void>;
  onLoadFlows: () => Promise<void>;
  onOpenPicker: () => Promise<boolean>;
  onOpenVersionHistory: () => void;
  onSave: () => Promise<void>;
  version: string;
}): ReactElement {
  return (
    <DropdownMenu onOpenChange={(open) => { if (open) void onLoadFlows(); }}>
      <DropdownMenuTrigger asChild>
        {/* 只承载版本与保存状态：流程名已在左侧面包屑里，重复一遍还得截断 */}
        <Button
          aria-label={`${label} · 版本与流程操作`}
          className="flex h-7 shrink-0 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50/60 px-2.5 text-[11px] text-slate-700 transition-all duration-150 hover:bg-white hover:border-slate-300 hover:shadow-xs"
          title={`${label} · 版本与流程操作`}
          variant="ghost"
        >
          <Workflow className="h-3.5 w-3.5 shrink-0 text-accent" strokeWidth={1.5} />
          <Badge className="border-slate-200 bg-white font-mono text-slate-600 text-[9px]">{version}</Badge>
          {/* dirty 只用文字徽标表达，不再另配一个同义的琥珀圆点 */}
          {dirty && <Badge className="border-amber-100 bg-amber-50 text-amber-700 text-[9px]">未保存</Badge>}
          {activeStatus === 'archived' && <Badge className="border-slate-200 bg-slate-100 text-slate-600 text-[9px]">归档</Badge>}
          <ChevronDown className="h-3 w-3 shrink-0 text-slate-400" strokeWidth={1.5} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-72">
        <DropdownMenuItem onSelect={() => void onOpenPicker()}>
          <BookOpen className="mr-2 h-3.5 w-3.5 text-slate-400" strokeWidth={1.5} />
          打开流程文件…
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onOpenVersionHistory}>
          <History className="mr-2 h-3.5 w-3.5 text-slate-400" strokeWidth={1.5} />
          版本历史
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void onSave()}>
          <Save className="mr-2 h-3.5 w-3.5 text-accent" strokeWidth={1.5} />
          保存当前版本
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void onExport()}>
          <Download className="mr-2 h-3.5 w-3.5 text-slate-400" strokeWidth={1.5} />
          另存为 JSON…
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={activeFlowId === undefined}
          onSelect={() => void onArchiveCurrent()}
        >
          <Archive className="mr-2 h-3.5 w-3.5 text-amber-500" strokeWidth={1.5} />
          归档当前版本
        </DropdownMenuItem>
        <DropdownMenuItem
          className="text-red-600 focus:text-red-600"
          disabled={activeFlowId === undefined}
          onSelect={() => void onDeleteCurrent()}
        >
          <Trash2 className="mr-2 h-3.5 w-3.5" strokeWidth={1.5} />
          删除当前版本
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500">最近版本</div>
        <div className="mb-1 rounded-lg bg-slate-50 px-3 py-2 text-[10px] leading-5 text-slate-500">
          {dirty ? '当前画布有未保存修改' : '当前画布已与版本快照同步'}
          {lastAutosavedAt !== null && (
            <span className="block font-mono text-slate-500">本地草稿 {formatFlowTime(lastAutosavedAt)}</span>
          )}
        </div>
        {activeFlowId !== undefined ? (
          <div className="mx-1 mb-1 rounded-lg bg-slate-50 px-3 py-2.5">
            <div className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
              <span className="min-w-0 flex-1 truncate text-[11px] font-semibold text-slate-700">{label}</span>
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-slate-500">
              <span className="font-mono">{version}</span>
              <span>·</span>
              <span>{activeStatus === 'archived' ? '归档' : activeStatus === 'active' ? '启用' : activeStatus === 'paused' ? '已暂停' : activeStatus === 'disabled' ? '已禁用' : '草稿'}</span>
            </div>
            <div className="mt-2" onPointerDown={(e) => e.stopPropagation()}>
              <FlowIdCopy flowId={activeFlowId} />
            </div>
          </div>
        ) : (
          <div className="px-3 py-2 text-[11px] text-slate-500">暂无已保存流程</div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function formatFlowTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}
