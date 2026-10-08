import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { TaskSnapshot } from '../../types/electron';
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

describe('RunHistoryList', () => {
  it('显示失败和待确认原因，并转义来自运行记录的内容', () => {
    const html = render([
      { ...run, status: 'error', error: '<script>失败原因</script>' },
      { ...run, taskId: 'confirmation', status: 'awaiting_confirmation', confirmationMessage: '请确认订单金额' },
    ]);
    expect(html).toContain('&lt;script&gt;失败原因&lt;/script&gt;');
    expect(html).not.toContain('<script>');
    expect(html).toContain('请确认订单金额');
    expect(html).toContain('等待操作');
  });

  it('展示运行进度，终态不会显示过时的错误或确认消息', () => {
    const html = render([{ ...run, status: 'running', progress: { ...run.progress, currentStep: 2 }, error: '旧错误', confirmationMessage: '旧确认' }]);
    expect(html).toContain('执行进度 2 / 3 步');
    expect(html).not.toContain('旧错误');
    expect(html).not.toContain('旧确认');
    const finished = render([{ ...run, error: '旧错误', confirmationMessage: '旧确认' }]);
    expect(finished).not.toContain('旧错误');
    expect(finished).not.toContain('旧确认');
    expect(finished).toContain(`运行 ID ${run.taskId}`);
    expect(finished).toContain('title="' + run.taskId + '"');
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
