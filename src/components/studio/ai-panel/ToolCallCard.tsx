import { toolDisplayStatus } from './toolResult';
import { CheckCircle2, ChevronRight, CircleAlert, CircleSlash, Loader2, ShieldAlert } from 'lucide-react';
import type { ReactElement } from 'react';
import { useState } from 'react';
import { cn } from '../../../lib/utils';
import { CodeBlock } from '../../ui/CodeBlock';
import type { ToolCallState } from './aiPanelTypes';

const TOOL_LABELS: Record<string, string> = {
  get_flow: '读取流程结构',
  lint_flow: '静态质量检查',
  validate_flow: '验证变量引用',
  create_flow: '生成流程',
  update_flow: '更新流程',
  run_flow: '运行流程',
  get_run_status: '查询运行状态',
  get_run_error: '分析运行错误',
  get_run_output: '读取运行结果',
  apply_node_fix: '修复节点配置',
  publish_flow: '发布流程',
  get_run_logs: '读取运行日志',
  list_node_types: '查询节点类型',
  inspect_page: '检查页面结构',
  interact_page: '操作页面',
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

export function ToolCallCard({
  toolCall,
  live = true,
  expanded: controlledExpanded,
  onToggle,
  onFocusNode,
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
  // A "running" card in a non-live (historical) conversation means the session ended mid-stream.
  const status = toolDisplayStatus(toolCall, live);
  const result = readObject(toolCall.result);
  const findings = (Array.isArray(result?.lint_findings) ? result.lint_findings : Array.isArray(result?.findings) ? result.findings : [])
    .map(readObject).filter((item): item is Record<string, unknown> => Boolean(item));
  const node = getPrimaryNodeRef(toolCall);
  const statusLabel = { running: '执行中', done: '已返回', error: '异常', blocked: '已阻断', stopped: '已中断' }[status];
  const parsedArgs = parseArgs(toolCall.args);
  const hasArgs = Object.keys(parsedArgs).length > 0;
  // guard 拦截（如失败预算耗尽）不是成功也不是报错，需单独展示，否则会被误读为已完成
  const blockMsg = status === 'blocked' || status === 'error'
    ? blockedMessage(toolCall) ?? (typeof result?.error === 'string' ? result.error : null) : null;

  return (
    <div
      className={cn(
        'w-full min-w-0 overflow-hidden rounded-md text-xs',
        status === 'error' ? 'bg-red-50/50'
          : status === 'blocked' ? 'bg-amber-50/50'
            : 'bg-transparent'
      )}
    >
      <button
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-black/2"
        onClick={toggle}
        type="button"
      >
        {status === 'running' ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin motion-reduce:animate-none text-accent" strokeWidth={2} />
        ) : status === 'error' ? (
          <CircleAlert className="h-3.5 w-3.5 shrink-0 text-red-500" strokeWidth={2} />
        ) : status === 'blocked' ? (
          <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-amber-500" strokeWidth={2} />
        ) : status === 'stopped' ? (
          <CircleSlash className="h-3.5 w-3.5 shrink-0 text-slate-500" strokeWidth={2} />
        ) : (
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-700" strokeWidth={2} />
        )}

        <span className={cn(
          'min-w-0 flex-1 font-medium',
          status === 'error' ? 'text-red-700' : status === 'blocked' ? 'text-amber-700' : 'text-slate-700'
        )}>
          {label}
        </span>

        <span className="sr-only">{statusLabel}</span>

        <ChevronRight
          className={cn('ml-auto h-3 w-3 shrink-0 text-slate-300 transition-transform', expanded && 'rotate-90')}
        />
      </button>

      {blockMsg && (
        <p className="px-2.5 pb-2 text-xs leading-relaxed text-slate-700 break-words">
          {blockMsg}
        </p>
      )}

      {expanded && (
        <div className="ml-5 border-l border-slate-200 pl-2">
          {node && onFocusNode && (
            <button type="button" className="m-2 text-xs text-accent underline underline-offset-4" onClick={() => onFocusNode(node.id)}>
              定位节点：{node.title ?? node.id}
            </button>
          )}
          {findings.length > 0 && (
            <ul className="space-y-2 px-3 py-2 text-xs leading-relaxed text-slate-700">
              {findings.map((finding, index) => (
                <li key={index} className="break-words">
                  <span className="font-medium">{finding.severity === 'error' || finding.blocks_run === true ? '阻断' : '提示'} · {String(finding.node_id ?? '流程')}</span>
                  <p>{String(finding.message ?? finding.issue ?? '')}</p>
                  {typeof finding.fix === 'string' && <p className="mt-1 text-slate-600">{finding.fix}</p>}
                </li>
              ))}
            </ul>
          )}
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

        </div>
      )}
    </div>
  );
}
