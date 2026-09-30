import { describe, expect, it } from 'vitest';

import type { RunDetail, TaskSnapshot } from '../types/electron';
import {
  failedNodeId,
  historicalNodeStates,
  historicalRuntimeStatus,
  recentRunSummary,
  selectLatestTerminalRun,
  toRuntimeLogs,
} from './recentRunContext';

function task(overrides: Partial<TaskSnapshot> = {}): TaskSnapshot {
  return {
    createdAt: '2026-09-29T01:00:00Z',
    flowName: '测试流程',
    mode: 'run',
    progress: { currentStep: 2, elapsedMs: 800, percent: 50, totalSteps: 4 },
    runConfig: { concurrency: 1, failureStrategy: 'stop', scope: 'full', screenshot: true },
    status: 'error',
    taskId: 'task-1',
    updatedAt: '2026-09-29T01:01:00Z',
    ...overrides,
  };
}

describe('recent run context', () => {
  it('selects the newest terminal run without mistaking an active run for history', () => {
    const running = task({ status: 'running', taskId: 'running' });
    const failed = task({ taskId: 'failed' });
    expect(selectLatestTerminalRun([running, failed])?.taskId).toBe('failed');
    expect(historicalRuntimeStatus(running)).toBeNull();
    expect(historicalRuntimeStatus(failed)).toBe('error');
  });

  it('maps backend logs and keeps unknown levels readable as info', () => {
    expect(toRuntimeLogs([
      { id: '1', level: 'fatal', message: '崩溃', nodeId: null, time: '01:01:00' },
      { detail: '等待超时', id: '2', level: 'error', message: '失败', nodeId: 'n2', time: '01:01:01' },
    ])).toEqual([
      { id: '1', level: 'info', message: '崩溃', time: '01:01:00' },
      { detail: '等待超时', id: '2', level: 'error', message: '失败', nodeId: 'n2', time: '01:01:01' },
    ]);
  });

  it('uses the last error log as the failure focus and falls back to execution evidence', () => {
    const detail: RunDetail = {
      logs: [
        { id: '1', level: 'error', message: '第一次失败', nodeId: 'n1', time: '01:00:00' },
        { id: '2', level: 'error', message: '最终失败', nodeId: 'n2', time: '01:00:01' },
      ],
      run: task({ error: null }),
    };
    expect(failedNodeId(detail)).toBe('n2');
    expect(recentRunSummary(detail)).toBe('最终失败');

    const evidenceOnly: RunDetail = {
      logs: [],
      run: task({ executionEvidence: [{
        browserUrl: null,
        durationMs: 20,
        inputs: [],
        nodeId: 'n3',
        nodeType: 'browser.click',
        outputs: [],
        status: 'error',
        unchangedPairs: [],
      }] }),
    };
    expect(failedNodeId(evidenceOnly)).toBe('n3');
    expect(historicalNodeStates(evidenceOnly.run)).toEqual({ n3: { status: 'error' } });
  });
});
