import { describe, expect, it } from 'vitest';

import type { FlowSnapshot } from '../types/electron';
import type { FlowListItem } from './taskCenter';
import { filterFlowItems } from './taskCenter';

function buildItem(state: FlowListItem['state'], name: string): FlowListItem {
  return {
    flow: {
      createdAt: '2026-09-01T00:00:00.000Z',
      definition: {},
      flowId: name,
      folderPath: '默认目录',
      inputVariables: [],
      name,
      snapshots: [],
      status: 'active',
      updatedAt: '2026-09-01T00:00:00.000Z',
      version: 'v1.0.0',
    } as FlowSnapshot,
    folderPath: '默认目录',
    lastRunStatus: state === 'failed' ? 'error' : null,
    nextRunAt: state === 'scheduled' ? '2026-09-02T00:00:00.000Z' : null,
    state,
    successRate: null,
  };
}

describe('filterFlowItems', () => {
  const items = [
    buildItem('running', '运行任务'),
    buildItem('failed', '失败任务'),
    buildItem('scheduled', '调度任务'),
    buildItem('published', '普通任务'),
  ];

  it('按运维状态和关键字筛选流程', () => {
    expect(filterFlowItems(items, '', 'failed').map((item) => item.flow.name)).toEqual(['失败任务']);
    expect(filterFlowItems(items, '任务', 'scheduled').map((item) => item.flow.name)).toEqual(['调度任务']);
    expect(filterFlowItems(items, '普通', 'all').map((item) => item.flow.name)).toEqual(['普通任务']);
  });
});
