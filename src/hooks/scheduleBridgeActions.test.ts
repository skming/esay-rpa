import { describe, expect, it } from 'vitest';
import { resolveScheduleTarget } from './scheduleBridgeActions';
import type { FlowSnapshot } from '../types/electron';

const flows = [
  { flowId: 'one', name: '流程一', defaultBrowserExecutor: 'extension' },
  { flowId: 'two', name: '流程二', defaultBrowserExecutor: 'playwright' },
] as FlowSnapshot[];

describe('调度流程绑定', () => {
  it('未编辑时保留绑定，选择所有流程时显式清空旧绑定', () => {
    expect(resolveScheduleTarget(flows, {})).toBeNull();
    const all = { flowId: undefined, flowIds: [], flowName: '所有流程', browserExecutor: 'playwright' };
    expect(resolveScheduleTarget(flows, { flowId: '__all__' })).toEqual(all);
    expect(resolveScheduleTarget(flows, { flowIds: [] })).toEqual(all);
  });

  it('单选的两种输入使用相同的名称与默认执行器', () => {
    const single = { flowId: 'one', flowIds: [], flowName: '流程一', browserExecutor: 'extension' };
    expect(resolveScheduleTarget(flows, { flowId: 'one' })).toEqual(single);
    expect(resolveScheduleTarget(flows, { flowIds: ['one'] })).toEqual(single);
  });

  it('多选清空单流程绑定，并以显式列表为准', () => {
    expect(resolveScheduleTarget(flows, { flowId: 'one', flowIds: ['one', 'two'] })).toEqual({
      flowId: undefined, flowIds: ['one', 'two'], flowName: '2 个流程', browserExecutor: 'playwright',
    });
  });

  it.each([{ flowId: 'missing' }, { flowIds: ['one', 'missing'] }])('拒绝不存在的目标 %j', (options) => {
    expect(() => resolveScheduleTarget(flows, options)).toThrow('未找到所选流程');
  });
});
