import { describe, expect, it } from 'vitest';
import { buildRuntimeVariablePayload, parseInputVariableValue } from './inputVariables';
import type { RuntimeVariable } from '../types/rpa';

function variable(type: RuntimeVariable['type'], value: string): RuntimeVariable {
  return { name: 'input', scope: '全局', type, value };
}

describe('输入变量类型', () => {
  it.each([
    ['String', '123', '123'], ['String', 'false', 'false'], ['Integer', ' +12 ', 12],
    ['Boolean', 'FALSE', false], ['List', '[1,"2"]', [1, '2']], ['Dict', '{"count":2}', { count: 2 }],
  ] as const)('按声明解析 %s', (type, value, expected) => {
    expect(parseInputVariableValue(variable(type, value))).toEqual(expected);
  });

  it.each([
    ['Integer', '12abc'], ['Integer', '1.5'], ['Integer', ''], ['Integer', '9007199254740992'],
    ['Boolean', 'yes'], ['List', '{}'], ['Dict', '[]'], ['List', 'secret-invalid-json'],
  ] as const)('拒绝非法 %s 且不回显原值', (type, value) => {
    expect(() => parseInputVariableValue(variable(type, value))).toThrow(`输入变量 input 必须为有效的 ${type}`);
  });

  it('载荷保留字符串与容器类型', () => {
    expect(buildRuntimeVariablePayload([
      { ...variable('String', '123'), name: 'code' },
      { ...variable('List', '[1,2]'), name: 'items' },
    ])).toEqual({ code: '123', items: [1, 2] });
  });
});
