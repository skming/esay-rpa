import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { backend } from '../lib/backendClient';
import type { AiModelsResult } from '../types/electron';
import { useAiModelCatalogStore } from './useAiModelCatalogStore';

const model = {
  id: 'test-model',
  label: 'Test Model',
  provider: 'test',
  env_key: 'TEST_API_KEY',
  context_window: 1000,
  configured: false,
};

describe('useAiModelCatalogStore.load', () => {
  beforeEach(() => {
    useAiModelCatalogStore.setState({ models: [], providers: [], status: 'idle' });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('将后端的厂商分组作为唯一来源', async () => {
    vi.spyOn(backend, 'listAiModels').mockResolvedValue({
      models: [model],
      default: model.id,
      providers: [],
    });

    await useAiModelCatalogStore.getState().load();

    expect(useAiModelCatalogStore.getState()).toMatchObject({
      models: [model],
      providers: [],
      status: 'ready',
    });
  });

  it('缺少 providers 时拒绝目录响应', async () => {
    vi.spyOn(backend, 'listAiModels').mockResolvedValue({
      models: [model],
      default: model.id,
    } as AiModelsResult);

    await useAiModelCatalogStore.getState().load();

    expect(useAiModelCatalogStore.getState()).toMatchObject({
      models: [],
      providers: [],
      status: 'error',
    });
  });
});
