import { describe, expect, it } from 'vitest';

import type { TaskSnapshot } from '../types/electron';
import { runOutputSummary } from './runPresentation';

function runWith(result: TaskSnapshot['result'], artifactCount = 0): TaskSnapshot {
  return {
    taskId: 'task-1', flowName: '采集流程', status: 'success', mode: 'run',
    createdAt: '', updatedAt: '',
    progress: { currentStep: 1, totalSteps: 1, percent: 100, elapsedMs: 1 },
    runConfig: { scope: 'full', failureStrategy: 'stop', screenshot: false, concurrency: 1 },
    result,
    artifacts: Array.from({ length: artifactCount }, (_, index) => ({
      artifactId: String(index), taskId: 'task-1', artifactType: 'dataset', filename: 'result.json',
      storageUrl: '', contentType: 'application/json', sizeBytes: 0, createdAt: '', metadata: {},
    })),
  };
}

describe('runOutputSummary', () => {
  it('区分合法空结果和没有结果', () => {
    expect(runOutputSummary(runWith({ url: '', selector: '', count: 0, values: [] }))).toBe('0 条');
    expect(runOutputSummary(runWith(null))).toBe('—');
  });

  it('没有采集结果时显示实际产物数量', () => {
    expect(runOutputSummary(runWith(null, 2))).toBe('2 个产物');
  });
});
