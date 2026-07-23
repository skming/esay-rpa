import { describe, expect, it } from 'vitest';

import type { RuntimeVariable } from '../types/rpa';
import { buildRuntimeVariableViews, mergeRuntimeVariables } from './runtimeVariables';

const baseVariables: RuntimeVariable[] = [
  { category: 'flow', name: 'username', sensitive: false, scope: '全局', type: 'String', value: 'zhangsan' },
  { category: 'flow', name: 'retry_count', sensitive: false, scope: '全局', type: 'Integer', value: '1' }
];

describe('runtimeVariables', () => {
  it('运行态变量应覆盖默认变量', () => {
    expect(
      mergeRuntimeVariables(baseVariables, [{ category: 'flow', name: 'username', sensitive: false, scope: '全局', type: 'String', value: 'lisi' }]).find(
        (item) => item.name === 'username'
      )?.value
    ).toBe('lisi');
  });

  it('应标记默认值、覆写值和运行时值来源', () => {
    const rows = buildRuntimeVariableViews(
      baseVariables,
      [{ category: 'flow', name: 'username', sensitive: false, scope: '全局', type: 'String', value: 'wangwu' }],
      [{ category: 'flow', name: 'retry_count', sensitive: false, scope: '局部', type: 'Integer', value: '3' }]
    );

    expect(rows).toEqual([
      {
        category: 'flow',
        name: 'username',
        scope: '全局',
        sensitive: false,
        type: 'String',
        value: 'wangwu',
        defaultValue: 'zhangsan',
        overrideValue: 'wangwu',
        runtimeValue: undefined,
        source: 'override'
      },
      {
        category: 'flow',
        name: 'retry_count',
        scope: '局部',
        sensitive: false,
        type: 'Integer',
        value: '3',
        defaultValue: '1',
        overrideValue: undefined,
        runtimeValue: '3',
        source: 'runtime'
      }
    ]);
  });

  it('运行时新增变量也应显示为运行时来源', () => {
    const rows = buildRuntimeVariableViews(baseVariables, [], [{ category: 'flow', name: 'session_id', sensitive: false, scope: '局部', type: 'String', value: 'abc-123' }]);

    expect(rows.at(-1)).toEqual({
      category: 'flow',
      name: 'session_id',
      scope: '局部',
      sensitive: false,
      type: 'String',
      value: 'abc-123',
      defaultValue: undefined,
      overrideValue: undefined,
      runtimeValue: 'abc-123',
      source: 'runtime'
    });
  });
});
