import { renderToStaticMarkup } from 'react-dom/server';
import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { buildFlowListItems } from '../../lib/taskCenter';
import type { FlowSnapshot, ScheduleRunSummary, ScheduleSnapshot } from '../../types/electron';
import { ScheduleListTable } from '../scheduler/ScheduleListTable';
import * as tooltips from '../ui/tooltip';
import { DropdownMenuItem } from '../ui/dropdown-menu';
import { FlowListTable } from './FlowListTable';
import { FilterBar } from './surfaces';

afterEach(() => vi.restoreAllMocks());

const flow: FlowSnapshot = {
  flowId: 'flow-1', name: '订单采集', version: 'v1.0.0', description: '', status: 'active',
  definition: { nodes: [], edges: [] }, inputVariables: [], revision: 1,
  folderPath: '默认目录', snapshots: [],
  acceptanceContract: { deliverables: [], requirements: [] },
  createdAt: '2026-10-08T00:00:00Z', updatedAt: '2026-10-08T01:00:00Z',
};

const schedule: ScheduleSnapshot = {
  scheduleId: 'schedule-1', name: '每日采集', cronExpression: '0 9 * * *', timezone: 'Asia/Shanghai',
  status: 'enabled', task: { mode: 'run', flowId: flow.flowId, flowName: flow.name },
  createdAt: '2026-10-08T00:00:00Z', updatedAt: '2026-10-08T01:00:00Z',
};

describe('任务列表', () => {
  it('失败原因按需查看，状态仍可进入对应流程历史', () => {
    const content = vi.spyOn(tooltips, 'TooltipContent');
    const trigger = vi.spyOn(tooltips, 'TooltipTrigger');
    const onHistory = vi.fn();
    const html = renderToStaticMarkup(<FlowListTable
      items={buildFlowListItems([{ ...flow, lastRunStatus: 'error' }], [], new Set())}
      onArchive={vi.fn()} onCopyId={vi.fn()} onDelete={vi.fn()} onEdit={vi.fn()} onExport={vi.fn()}
      onHistory={onHistory} onRun={vi.fn()} onSetStatus={vi.fn()} onStop={vi.fn()}
      runErrors={{ [flow.flowId]: '脚本执行失败，完整原因' }}
    />);
    expect(html).toContain('上次失败');
    expect(html).not.toContain('脚本执行失败，完整原因');
    expect(content.mock.calls.some(([props]) => props.children === '脚本执行失败，完整原因')).toBe(true);
    const status = trigger.mock.calls.find(([props]) => props['aria-label'] === '上次失败，查看 订单采集 的运行历史');
    expect(status).toBeDefined();
    status?.[0].onClick?.({} as never);
    expect(onHistory).toHaveBeenCalledWith(flow.flowId);
  });

  it('普通行不再常驻显示 ID，编辑与运行入口仍有明确名称', () => {
    const html = renderToStaticMarkup(<FlowListTable
      items={buildFlowListItems([flow], [], new Set())}
      onArchive={vi.fn()} onCopyId={vi.fn()} onDelete={vi.fn()} onEdit={vi.fn()} onExport={vi.fn()}
      onHistory={vi.fn()} onRun={vi.fn()} onSetStatus={vi.fn()} onStop={vi.fn()} runErrors={{}}
    />);
    expect(html).not.toContain(flow.flowId);
    expect(html).toContain('编辑 订单采集');
    expect(html).toContain('运行 订单采集');
  });

  it('复制流程 ID 移入更多操作后仍复制当前行的完整 ID', () => {
    const onCopyId = vi.fn(async () => {});
    const tree = FlowListTable({
      items: buildFlowListItems([flow], [], new Set()), onArchive: vi.fn(), onCopyId, onDelete: vi.fn(),
      onEdit: vi.fn(), onExport: vi.fn(), onHistory: vi.fn(), onRun: vi.fn(), onSetStatus: vi.fn(), onStop: vi.fn(), runErrors: {},
    });
    type Props = { children?: ReactNode; onClick?: () => void };
    function elements(node: ReactNode): ReactElement<Props>[] {
      if (Array.isArray(node)) return node.flatMap(elements);
      if (!isValidElement<Props>(node)) return [];
      return [node, ...elements(node.props.children)];
    }
    const copyItem = elements(tree).find((element) => element.type === DropdownMenuItem
      && Array.isArray(element.props.children) && element.props.children.includes('复制流程 ID'));
    expect(copyItem).toBeDefined();
    copyItem?.props.onClick?.();
    expect(onCopyId).toHaveBeenCalledWith(flow.flowId);
  });
});

describe('调度列表', () => {
  const electron = {
    flows: [], getRunDetail: vi.fn(async () => { throw new Error('Task not found'); }), pushToast: vi.fn(),
  } as unknown as ElectronBridgeState;

  it('调度异常收进状态 tooltip，Cron 和时区仍可查阅', () => {
    const content = vi.spyOn(tooltips, 'TooltipContent');
    const html = renderToStaticMarkup(<ScheduleListTable electron={electron} runSummaries={{}} schedules={[{
      ...schedule, lastError: '<script>触发失败</script>', nextRunAt: null,
    }]} />);
    expect(html).toContain('需处理');
    expect(html).toContain('查看 每日采集 的调度配置');
    expect(html).not.toContain('触发失败');
    expect(content.mock.calls.some(([props]) => props.children === '<script>触发失败</script>')).toBe(true);
    const rule = content.mock.calls.find(([props]) => Array.isArray(props.children));
    expect(rule).toBeDefined();
    const ruleHtml = renderToStaticMarkup(<div>{rule?.[0].children}</div>);
    expect(ruleHtml).toContain('0 9 * * *');
    expect(ruleHtml).toContain('Asia/Shanghai');
  });

  it('部分失败按批次统计解释，停用后不把历史失败标为当前异常', () => {
    const content = vi.spyOn(tooltips, 'TooltipContent');
    const summary: ScheduleRunSummary = {
      scheduleId: schedule.scheduleId, status: 'partial', total: 3, success: 1, failed: 1, stopped: 1,
      running: 0, taskIds: ['task-1', 'task-2', 'task-3'],
    };
    const html = renderToStaticMarkup(<ScheduleListTable electron={electron} runSummaries={{ [schedule.scheduleId]: summary }} schedules={[schedule]} />);
    expect(html).toContain('查看 每日采集 的运行结果');
    expect(content.mock.calls.some(([props]) => props.children === '最近一批部分成功：成功 1，失败 1，停止 1，共 3 个流程')).toBe(true);
    const disabled = renderToStaticMarkup(<ScheduleListTable electron={electron} runSummaries={{ [schedule.scheduleId]: summary }} schedules={[{ ...schedule, status: 'disabled' }]} />);
    expect(disabled).not.toContain('需处理');
    expect(disabled).toContain('停用');
  });

  it('点击异常状态按批次中的任务 ID 打开结果', async () => {
    const trigger = vi.spyOn(tooltips, 'TooltipTrigger');
    const getRunDetail = vi.fn(async () => { throw new Error('Task not found'); });
    const summary: ScheduleRunSummary = {
      scheduleId: schedule.scheduleId, status: 'failed', total: 2, success: 0, failed: 2, stopped: 0,
      running: 0, taskIds: ['task-1', 'task-2'],
    };
    renderToStaticMarkup(<ScheduleListTable electron={{ ...electron, getRunDetail } as unknown as ElectronBridgeState}
      runSummaries={{ [schedule.scheduleId]: summary }} schedules={[schedule]} />);
    const status = trigger.mock.calls.find(([props]) => props['aria-label'] === '需处理，查看 每日采集 的运行结果');
    expect(status).toBeDefined();
    status?.[0].onClick?.({} as never);
    expect(getRunDetail.mock.calls).toEqual([['task-1'], ['task-2']]);
    await Promise.resolve();
  });
});

it('筛选结果与重置入口共用筛选栏，并保留结果状态通知', () => {
  const html = renderToStaticMarkup(<FilterBar
    counts={{ all: 3, failed: 1 }} filters={[{ label: '全部', value: 'all' }, { label: '失败', value: 'failed' }]}
    onQueryChange={vi.fn()} onValueChange={vi.fn()} onReset={vi.fn()} query="订单"
    searchLabel="搜索流程" searchPlaceholder="搜索名称" statusText="显示 1 / 3 个流程" value="failed"
  />);
  expect(html).toContain('显示 1 / 3 个流程');
  expect(html).toContain('清除筛选');
  expect(html).toContain('aria-live="polite"');
  expect(html).toContain('aria-pressed="true"');
});
