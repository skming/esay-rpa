import { CheckCircle2, ChevronRight, ChevronUp, CircleAlert, CircleSlash, Loader2, ShieldAlert } from 'lucide-react';
import type { ReactElement } from 'react';
import { useState } from 'react';
import { cn } from '../../../lib/utils';
import { CodeBlock } from '../../ui/CodeBlock';
import type { ToolCallState } from './aiPanelTypes';

const TOOL_LABELS: Record<string, string> = {
  get_flow: '读取流程结构',
  lint_flow: '静态质量检查',
  validate_flow: '验证变量引用',
  create_flow: '创建新流程',
  update_flow: '生成变更方案',
  run_flow: '运行流程',
  get_run_status: '查询运行状态',
  get_run_error: '分析运行错误',
  get_run_output: '读取运行结果',
  apply_node_fix: '修复节点配置',
  publish_flow: '发布流程',
  get_run_logs: '读取运行日志',
  list_node_types: '查询节点类型',
  inspect_page: '检查页面结构',
  inspect_screenshot: '截图查看页面',
  assert_run_output: '审计运行输出',
  set_acceptance_contract: '更新验收标准',
  list_flows: '列出流程',
  check_extension_connection: '检查扩展连接',
  stop_run: '停止运行任务',
  list_schedules: '列出定时任务',
  create_schedule: '创建定时任务',
  toggle_schedule: '切换定时任务',
};

function parseArgs(raw: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(raw || '{}');
    return parsed !== null && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

type NodeRef = {
  id: string;
  title?: string;
  type?: string;
  label?: string;
};

function readObject(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function readNodeRef(value: unknown): NodeRef | undefined {
  const obj = readObject(value);
  if (!obj || typeof obj.id !== 'string') return undefined;
  return {
    id: obj.id,
    title: typeof obj.title === 'string' ? obj.title : undefined,
    type: typeof obj.type === 'string' ? obj.type : undefined,
    label: typeof obj.label === 'string' ? obj.label : undefined,
  };
}

function fallbackNodeRef(id: unknown): NodeRef | undefined {
  return typeof id === 'string' && id ? { id } : undefined;
}

function getPrimaryNodeRef(toolCall: ToolCallState): NodeRef | undefined {
  const args = parseArgs(toolCall.args);
  const result = readObject(toolCall.result);
  return readNodeRef(result?.node_ref)
    ?? fallbackNodeRef(result?.node_id)
    ?? fallbackNodeRef(args.node_id);
}

/** Guard tools return a human-readable `message` explaining why the call was blocked. */
function blockedMessage(toolCall: ToolCallState): string | null {
  const result = readObject(toolCall.result);
  return typeof result?.message === 'string' ? result.message : null;
}

function summarize(toolCall: ToolCallState): string | null {
  const args = parseArgs(toolCall.args);
  const result = toolCall.result as Record<string, unknown> | undefined;

  const flowName = typeof args.name === 'string' ? args.name : undefined;
  const nodeId = typeof args.node_id === 'string' ? args.node_id : undefined;

  switch (toolCall.tool) {
    case 'create_flow':
    case 'update_flow':
      return flowName ?? null;
    case 'run_flow':
      return typeof result?.task_id === 'string' ? `任务 ${(result.task_id).slice(0, 8)}` : null;
    case 'get_run_output': {
      const vars = Array.isArray(result?.artifacts) ? (result!.artifacts as unknown[]).length : null;
      return vars !== null ? `${vars} 个产物` : null;
    }
    case 'apply_node_fix':
      return getPrimaryNodeRef(toolCall)?.title ?? nodeId ?? null;
    case 'list_node_types':
      return Array.isArray(result?.types) ? `${(result!.types as unknown[]).length} 种节点` : null;
    default:
      return null;
  }
}

export function ToolCallCard({
  toolCall,
  live = true,
  expanded: controlledExpanded,
  onToggle,
}: {
  toolCall: ToolCallState;
  live?: boolean;
  /** 传入即受控，由外层做「同时只展开一条」；不传则自管，供单独渲染的场景使用 */
  expanded?: boolean;
  onToggle?: () => void;
  onFocusNode?: (nodeId: string) => void;
}): ReactElement {
  const [selfExpanded, setSelfExpanded] = useState(false);
  const expanded = controlledExpanded ?? selfExpanded;
  const toggle = onToggle ?? ((): void => setSelfExpanded((value) => !value));
  const label = TOOL_LABELS[toolCall.tool] ?? toolCall.tool;
  const detail = summarize(toolCall);
  // A "running" card in a non-live (historical) conversation means the session ended mid-stream.
  const status = (!live && toolCall.status === 'running') ? 'stopped' : toolCall.status;
  const parsedArgs = parseArgs(toolCall.args);
  const hasArgs = Object.keys(parsedArgs).length > 0;
  // guard 拦截（如失败预算耗尽）不是成功也不是报错，需单独展示，否则会被误读为已完成
  const blockMsg = status === 'blocked' ? blockedMessage(toolCall) : null;

  return (
    <div
      className={cn(
        'my-1 w-full overflow-hidden rounded-lg border text-[11px] transition-colors',
        status === 'error' ? 'border-red-200 bg-red-50/50'
          : status === 'blocked' ? 'border-amber-200 bg-amber-50/50'
            : 'border-slate-200 bg-slate-50/80'
      )}
    >
      <button
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-black/2"
        onClick={toggle}
        type="button"
      >
        {status === 'running' ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" strokeWidth={2} />
        ) : status === 'error' ? (
          <CircleAlert className="h-3.5 w-3.5 shrink-0 text-red-500" strokeWidth={2} />
        ) : status === 'blocked' ? (
          <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-amber-500" strokeWidth={2} />
        ) : status === 'stopped' ? (
          <CircleSlash className="h-3.5 w-3.5 shrink-0 text-slate-400" strokeWidth={2} />
        ) : (
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-500" strokeWidth={2} />
        )}

        <span className={cn(
          'font-medium',
          status === 'error' ? 'text-red-700' : status === 'blocked' ? 'text-amber-700' : 'text-slate-700'
        )}>
          {label}{status === 'blocked' && ' · 已阻断'}
        </span>

        {detail && (
          <span className="min-w-0 truncate rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[10px] font-medium text-slate-500">
            {detail}
          </span>
        )}

        <ChevronRight
          className={cn('ml-auto h-3 w-3 shrink-0 text-slate-300 transition-transform', expanded && 'rotate-90')}
        />
      </button>

      {blockMsg && (
        <p className="border-t border-amber-200/70 px-2.5 py-1.5 text-[10.5px] leading-relaxed text-amber-700">
          {blockMsg}
        </p>
      )}

      {expanded && (
        <div className="border-t border-slate-200/80 bg-white">
          {hasArgs && (
            <div className="px-2.5 pt-2">
              <p className="mb-1 text-[9.5px] font-semibold uppercase tracking-wide text-slate-500">参数</p>
              <CodeBlock code={JSON.stringify(parsedArgs, null, 2)} language="json" maxHeight={128} variant="light" />
            </div>
          )}
          {toolCall.result !== undefined && (
            <div className="px-2.5 py-2">
              <p className="mb-1 text-[9.5px] font-semibold uppercase tracking-wide text-slate-500">结果</p>
              <CodeBlock code={JSON.stringify(toolCall.result, null, 2)} language="json" maxHeight={192} variant="light" />
            </div>
          )}
          {/* 展开体高到读完就看不见标题行了，底部再给一个收起入口，省掉反向滚动去找折叠箭头 */}
          <button
            className="flex w-full items-center justify-center gap-1 border-t border-slate-100 py-1.5 text-[10px] font-medium text-slate-400 transition-colors hover:bg-slate-50 hover:text-slate-600"
            onClick={toggle}
            type="button"
          >
            <ChevronUp className="h-3 w-3" strokeWidth={2} />
            收起
          </button>
        </div>
      )}
    </div>
  );
}
