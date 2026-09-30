import { beforeEach, describe, expect, it } from 'vitest';

import { useFlowVariableStore } from './useFlowVariableStore';
import type { RuntimeVariable } from '../types/rpa';

const variable = (name: string): RuntimeVariable => ({
  category: 'flow',
  name,
  scope: '全局',
  sensitive: false,
  type: 'String',
  value: '',
});

describe('useFlowVariableStore', () => {
  beforeEach(() => {
    useFlowVariableStore.setState({ inputVariables: [variable('keyword'), variable('keyword_2')] });
  });

  it('returns the normalized name used by the store after a rename', () => {
    const updated = useFlowVariableStore.getState().updateInputVariable('keyword', { name: 'keyword_2' });

    expect(updated?.name).toBe('keyword_2_2');
    expect(useFlowVariableStore.getState().inputVariables.map((item) => item.name)).toEqual(['keyword_2_2', 'keyword_2']);
  });
});
