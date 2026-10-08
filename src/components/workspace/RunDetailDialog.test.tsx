import type { ComponentProps } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import type { TaskSnapshot } from '../../types/electron';
import { RunDetailDialog } from './RunDetailDialog';

// Portal 不参与服务端渲染；仅替换弹框容器，保留实际 Tabs 状态与内容。
vi.mock('../ui/dialog', () => {
  const Container = ({ children }: ComponentProps<'div'>) => <div>{children}</div>;
  return {
    Dialog: Container,
    DialogContent: Container,
    DialogHeader: Container,
    DialogTitle: Container,
    DialogBody: Container,
    DialogFooter: Container,
  };
});
vi.mock('../ui/tabs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../ui/tabs')>();
  return {
    ...actual,
    TabsContent: (props: ComponentProps<typeof actual.TabsContent>) => <actual.TabsContent {...props} keepMounted />,
  };
});
vi.mock('../studio/bottom-panel/ArtifactPreviewDialog', () => ({ ArtifactPreviewDialog: () => null }));

const run: TaskSnapshot = {
  taskId: 'task-detail-test',
  flowName: '订单采集',
  status: 'success',
  mode: 'run',
  runConfig: { scope: 'full', failureStrategy: 'stop', screenshot: false, concurrency: 1 },
  progress: { currentStep: 3, totalSteps: 3, percent: 100, elapsedMs: 27000 },
  createdAt: '2026-10-08T03:00:00Z',
  updatedAt: '2026-10-08T03:00:27Z',
};
function render(snapshot: TaskSnapshot | null): string {
  return renderToStaticMarkup(
    <RunDetailDialog open run={snapshot} onOpenChange={() => {}} onLoadDetail={async () => ({ run, logs: [] })} />,
  );
}

describe('RunDetailDialog', () => {
  it.each([
    ['error', '日志'],
    ['running', '日志'],
    ['success', '结果'],
    ['queued', '结果'],
    ['stopped', '结果'],
    ['awaiting_confirmation', '结果'],
  ] as const)('按 %s 状态选择默认页', (status, expected) => {
    const html = render({ ...run, status });
    const active = html.match(/<button(?=[^>]*role="tab")(?=[^>]*aria-selected="true")[^>]*>(.*?)<\/button>/s)?.[1];
    expect(active).toBeDefined();
    expect(active).toMatch(new RegExp(`^${expected}`));
  });

  it('敏感值和凭据不会进入 DOM、展开内容或复制属性', () => {
    const html = render({
      ...run,
      variables: [
        { name: 'token', type: 'String', scope: '全局', value: 'SENSITIVE_TEST_VALUE', sensitive: true },
        {
          name: 'password',
          category: 'credential',
          type: 'String',
          scope: '全局',
          value: 'CREDENTIAL_TEST_VALUE',
          sensitive: false,
        },
        { name: 'orders', type: 'List', scope: '全局', value: 'ORDINARY_TEST_VALUE' },
      ],
    });
    expect(html).not.toContain('SENSITIVE_TEST_VALUE');
    expect(html).not.toContain('CREDENTIAL_TEST_VALUE');
    expect(html).not.toContain('复制变量 token');
    expect(html).not.toContain('复制变量 password');
    expect(html).toContain('ORDINARY_TEST_VALUE');
    expect(html).toContain('复制变量 orders');
  });

  it('只展示当前状态对应的错误和操作提示，并转义结果数据', () => {
    const success = render({
      ...run,
      error: '过期错误',
      confirmationMessage: '过期确认',
      result: { url: '', selector: '', count: 1, values: ['<script>示例</script>'] },
    });
    expect(success).not.toContain('过期错误');
    expect(success).not.toContain('过期确认');
    expect(success).toContain('&lt;script&gt;示例&lt;/script&gt;');
    expect(success).not.toContain('<script>');
    const awaiting = render({ ...run, status: 'awaiting_confirmation', confirmationMessage: '核对金额后继续' });
    expect(awaiting).toContain('核对金额后继续');
    expect(render(null)).toBe('');
  });
});
