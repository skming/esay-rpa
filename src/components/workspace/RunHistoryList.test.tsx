import { renderToStaticMarkup } from 'react-dom/server';
import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { TaskSnapshot } from '../../types/electron';
import { TooltipContent, TooltipTrigger } from '../ui/tooltip';
import { RunHistoryList } from './RunHistoryList';

const run: TaskSnapshot = {
  taskId: 't_01234567-89ab-cdef-0123-456789abcdef',
  flowName: '订单采集',
  status: 'success',
  mode: 'run',
  runConfig: { scope: 'full', failureStrategy: 'stop', screenshot: false, concurrency: 1 },
  progress: { currentStep: 3, totalSteps: 3, percent: 100, elapsedMs: 27000 },
  createdAt: '2026-10-08T03:00:00Z',
  updatedAt: '2026-10-08T03:00:27Z',
};

function render(runs: TaskSnapshot[], pagination: { hasMore?: boolean; loadingMore?: boolean } = {}): string {
  return renderToStaticMarkup(<RunHistoryList onInspectRun={() => {}} onRefresh={() => {}} onLoadMore={() => {}} runs={runs} {...pagination} />);
}

type ElementProps = { children?: ReactNode; onClick?: () => void; 'aria-label'?: string };

function elements(node: ReactNode): ReactElement<ElementProps>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<ElementProps>(node)) return [];
  return [node, ...elements(node.props.children)];
}

function tooltipContents(runs: TaskSnapshot[]): ReactNode[] {
  const tree = RunHistoryList({ onInspectRun: () => {}, onRefresh: () => {}, runs });
  return elements(tree).filter((element) => element.type === TooltipContent).map((element) => element.props.children);
}

describe('RunHistoryList', () => {
  it('原因放进状态 tooltip，列表中只保留状态，内容仍安全转义', () => {
    const runs: TaskSnapshot[] = [
      { ...run, status: 'error', error: '<script>失败原因</script>' },
      { ...run, taskId: 'confirmation', status: 'awaiting_confirmation', confirmationMessage: '请确认订单金额' },
    ];
    const html = render(runs);
    expect(html).not.toContain('失败原因');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('请确认订单金额');
    expect(html).toContain('失败，查看 订单采集 的运行详情');
    expect(html).toContain('等待操作');
    const contents = tooltipContents(runs);
    expect(contents).toEqual(['<script>失败原因</script>', '请确认订单金额']);
    expect(renderToStaticMarkup(<div>{contents[0]}</div>)).toContain('&lt;script&gt;失败原因&lt;/script&gt;');
  });

  it('运行进度按需查看，终态不会显示过时的错误或确认消息', () => {
    const running: TaskSnapshot = { ...run, status: 'running', progress: { ...run.progress, currentStep: 2 }, error: '旧错误', confirmationMessage: '旧确认' };
    const html = render([running]);
    expect(html).not.toContain('执行进度');
    expect(tooltipContents([running])).toEqual(['执行进度 2 / 3 步']);
    expect(html).not.toContain('旧错误');
    expect(html).not.toContain('旧确认');
    const finished = render([{ ...run, error: '旧错误', confirmationMessage: '旧确认' }]);
    expect(finished).not.toContain('旧错误');
    expect(finished).not.toContain('旧确认');
    expect(finished).toContain(`运行 ID ${run.taskId}`);
    expect(finished).not.toContain('title="' + run.taskId + '"');
    expect(tooltipContents([{ ...run, error: '旧错误', confirmationMessage: '旧确认' }])).toEqual([]);
  });

  it('状态按钮可打开详情，无具体原因时提供提示', () => {
    const failed: TaskSnapshot = { ...run, status: 'error', error: '  ' };
    const onInspectRun = vi.fn();
    const tree = RunHistoryList({ onInspectRun, onRefresh: () => {}, runs: [failed] });
    const trigger = elements(tree).find((element) => element.type === TooltipTrigger);
    expect(trigger?.props['aria-label']).toBe('失败，查看 订单采集 的运行详情');
    expect(trigger?.props.onClick).toBeTypeOf('function');
    trigger?.props.onClick?.();
    expect(onInspectRun).toHaveBeenCalledWith(failed);
    expect(tooltipContents([failed])).toEqual(['运行失败，打开详情查看执行证据']);
    expect(tooltipContents([{ ...run, status: 'awaiting_confirmation' }])).toEqual(['流程正在等待操作确认']);
  });

  it('保留空态，并按分页状态显示和禁用加载操作', () => {
    expect(render([])).toContain('暂无运行记录');
    expect(render([run], { hasMore: false })).not.toContain('加载更多');
    expect(render([run], { hasMore: true })).toContain('加载更多');
    const html = render([run], { hasMore: true, loadingMore: true });
    expect(html).toContain('加载中…');
    expect(html).toMatch(/<button(?=[^>]*aria-busy="true")(?=[^>]*disabled="")[^>]*>/);
  });
});
