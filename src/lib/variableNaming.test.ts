import { describe, expect, it } from 'vitest';

import { isSafeVariableName, normalizeVariableName, validateVariableNameInput } from './variableNaming';

describe('variableNaming', () => {
  it('会规范化非法变量名', () => {
    expect(normalizeVariableName(' 1 order id ')).toBe('order_id');
  });

  it('识别安全变量名', () => {
    expect(isSafeVariableName('result_status')).toBe(true);
    expect(isSafeVariableName('1-result')).toBe(false);
  });

  it('提示引用不存在的变量', () => {
    expect(validateVariableNameInput('missing_value', { existingNames: ['known_value'], mode: 'reference' })).toEqual({
      issue: 'missing_reference',
      message: '当前流程和运行态中未找到该变量',
      normalizedValue: 'missing_value'
    });
  });

  it('提示目标变量会覆盖已有值', () => {
    expect(validateVariableNameInput('known_value', { existingNames: ['known_value'], mode: 'target' })).toEqual({
      issue: 'overwrite_target',
      message: '该变量已存在，运行时会覆盖当前值',
      normalizedValue: 'known_value'
    });
  });

  it('提示非法变量名的规范化结果', () => {
    expect(validateVariableNameInput('9 result value', { existingNames: [], mode: 'target' })).toEqual({
      issue: 'invalid',
      message: '变量名将被规范化为 result_value',
      normalizedValue: 'result_value'
    });
  });
});
