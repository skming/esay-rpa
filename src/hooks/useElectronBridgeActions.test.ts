import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { initialEdges, initialNodes } from '../data/studioData';
import { fetchFlowSnapshot } from '../lib/backendClient';
import { buildFlowDefinition, restoreFlowCanvas } from '../lib/flowDefinition';
import type { BridgeResult, FlowSnapshot, FlowVersionSnapshot } from '../types/electron';
import type { RuntimeVariable } from '../types/rpa';
import { useElectronBridgeActions, type ElectronBridgeActions } from './useElectronBridgeActions';

vi.mock('../lib/backendClient', () => ({ backend: {}, fetchFlowSnapshot: vi.fn() }));
vi.mock('./useFlowDraftAutosave', () => ({ clearDraftStorage: vi.fn() }));

const savedVariables: RuntimeVariable[] = [{ name: 'query', scope: '全局', type: 'String', value: 'saved' }];
const draftVariables: RuntimeVariable[] = [{ name: 'query', scope: '全局', type: 'String', value: 'draft' }];
const savedFlow: FlowSnapshot = {
  createdAt: '2026-09-05T00:00:00.000Z',
  definition: buildFlowDefinition(initialNodes, initialEdges, savedVariables),
  flowId: 'flow-1',
  folderPath: '默认目录',
  inputVariables: savedVariables,
  name: '测试流程',
  snapshots: [],
  status: 'active',
  updatedAt: '2026-09-05T00:00:00.000Z',
  version: 'v1.0.0',
};

function renderActions(
  callBridge: Parameters<typeof useElectronBridgeActions>[0]['callBridge'] = async () => null,
  currentFlow: FlowSnapshot | null = null,
) {
  let variables = draftVariables;
  const params: Parameters<typeof useElectronBridgeActions>[0] = {
    activeFlowNameRef: { current: '' },
    activeRunId: null,
    activeRunFlowId: null,
    callBridge,
    clearLastRunOverrides: vi.fn(),
    currentFlow,
    flowCanvas: { nodes: initialNodes, edges: initialEdges },
    flows: [],
    pushToast: vi.fn(() => 1),
    dismissToast: vi.fn(),
    inputVariables: draftVariables,
    runtimeVariables: [],
    resetRunView: vi.fn(),
    setLastRunOverrides: vi.fn(),
    setCurrentFlow: vi.fn(),
    setFlowEdges: vi.fn(),
    setFlowNodes: vi.fn(),
    setFlows: vi.fn(),
    setInputVariables: vi.fn((next: RuntimeVariable[]) => { variables = next; }),
    setActiveRunId: vi.fn(),
    setActiveRunFlowId: vi.fn(),
    setArtifactContent: vi.fn(),
    setArtifacts: vi.fn(),
    setGeneratedScript: vi.fn(),
    setQueueStats: vi.fn(),
    setRuntimeStatus: vi.fn(),
    setRuns: vi.fn(),
    setSchedules: vi.fn(),
    setScheduleRunSummaries: vi.fn(),
    setSiteAnalysis: vi.fn(),
    setVariables: vi.fn(),
    setLogs: vi.fn(),
    setConfirmationMessage: vi.fn(),
    setActivePickerRequest: vi.fn(),
    setCanvasFitVersion: vi.fn(),
  };
  let actions!: ElectronBridgeActions;
  function Probe() {
    actions = useElectronBridgeActions(params);
    return null;
  }
  renderToStaticMarkup(createElement(Probe));
  return { actions, params, readVariables: () => variables };
}

describe('流程恢复的输入变量归属', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchFlowSnapshot).mockResolvedValue({ kind: 'ok', flow: savedFlow });
  });

  it('草稿已恢复时只补流程元数据，保留草稿变量和画布', async () => {
    const { actions, params, readVariables } = renderActions();

    await actions.silentlyRestoreCurrentFlow(savedFlow.flowId);

    expect(params.setCurrentFlow).toHaveBeenCalledWith(savedFlow);
    expect(readVariables()).toEqual(draftVariables);
    expect(params.setInputVariables).not.toHaveBeenCalled();
    expect(params.setFlowNodes).not.toHaveBeenCalled();
    expect(params.setFlowEdges).not.toHaveBeenCalled();
  });

  it('无草稿时完整恢复画布并替换为保存的输入变量', async () => {
    const { actions, params, readVariables } = renderActions();

    await actions.silentlyRestoreCurrentFlow(savedFlow.flowId, { restoreCanvas: true });

    expect(params.setCurrentFlow).toHaveBeenCalledWith(savedFlow);
    expect(readVariables()).toEqual(savedVariables);
    expect(params.setFlowNodes).toHaveBeenCalledOnce();
    expect(params.setFlowEdges).toHaveBeenCalledOnce();
  });

  it('新建流程清空上一个流程的变量', async () => {
    const createFlow = vi.fn(async (payload) => ({ ok: true as const, data: { ...savedFlow, ...payload, flowId: 'persisted-draft' } }));
    const { actions, params, readVariables } = renderActions(async (action) => {
      const result = await action({ createFlow } as unknown as import('../types/electron').RpaBridge);
      return result.ok ? result.data ?? null : null;
    });

    expect(await actions.createNewFlow('新流程')).toBe(true);
    expect(createFlow).toHaveBeenCalledWith(expect.objectContaining({ name: '新流程', status: 'draft' }));
    expect(params.setFlows).toHaveBeenCalledOnce();

    expect(params.setCurrentFlow).toHaveBeenCalledWith(expect.objectContaining({ name: '新流程', inputVariables: [] }));
    const created = vi.mocked(params.setCurrentFlow).mock.calls[0][0] as FlowSnapshot;
    expect(restoreFlowCanvas(created.definition)?.nodes.map((node) => node.id)).toEqual(initialNodes.map((node) => node.id));
    expect(readVariables()).toEqual([]);
    expect(created.flowId).toBe('persisted-draft');
  });

  it('后端创建失败时保留当前流程与画布', async () => {
    const { actions, params, readVariables } = renderActions();
    expect(await actions.createNewFlow('新流程')).toBe(false);
    expect(params.setCurrentFlow).not.toHaveBeenCalled();
    expect(params.setFlowNodes).not.toHaveBeenCalled();
    expect(params.resetRunView).not.toHaveBeenCalled();
    expect(readVariables()).toEqual(draftVariables);
  });

  it('恢复快照时同时恢复定义、变量和验收契约', async () => {
    const updateFlow = vi.fn(async () => ({ ok: true as const, data: { ...savedFlow, revision: 3 } }));
    const snapshot: FlowVersionSnapshot = {
      acceptanceContract: {
        requirements: [{ id: 'required', description: '必须有结果', sourceKind: 'user' }],
        deliverables: [{ id: 'rows', kind: 'table', requirementIds: ['required'], variable: 'rows' }],
      },
      definition: { nodes: [{ id: 'old' }], edges: [] },
      inputVariables: savedVariables,
      revision: 1,
      savedAt: '2026-09-04T00:00:00.000Z',
      version: 'v1.0.0',
    };
    const { actions } = renderActions(async (action) => {
      const result = await action({ updateFlow } as unknown as import('../types/electron').RpaBridge);
      return result.ok ? result.data ?? null : null;
    }, savedFlow);

    expect(await actions.rollbackFlowSnapshot(snapshot)).toBe(true);
    expect(updateFlow).toHaveBeenCalledWith(savedFlow.flowId, expect.objectContaining({
      acceptanceContract: snapshot.acceptanceContract,
      definition: snapshot.definition,
      inputVariables: snapshot.inputVariables,
    }));
  });
});

describe('导入流程文件', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // openFlow 与 createFlow 都经 callBridge 调用，桥接层按被调方法名分发并解包 BridgeResult。
  function importBridge(fileContent: string, createFlow: ReturnType<typeof vi.fn>) {
    const openFlow = vi.fn(async () => ({ ok: true as const, data: { canceled: false, name: '导入.rpa.json', content: fileContent } }));
    const call = async <T,>(action: (api: import('../types/electron').RpaBridge) => Promise<BridgeResult<T>>): Promise<T | null> => {
      const result = await action({ openFlow, createFlow } as unknown as import('../types/electron').RpaBridge);
      return result.ok ? result.data ?? null : null;
    };
    return { call, openFlow };
  }

  it('合法 JSON 但不是流程文件时报错，不创建流程也不动画布', async () => {
    const createFlow = vi.fn();
    const { call } = importBridge('{"dependencies":{"react":"^19"}}', createFlow);
    const { actions, params } = renderActions(call);

    expect(await actions.openFlow()).toBe(false);
    expect(params.pushToast).toHaveBeenCalledWith('error', '该文件不是有效的流程文件');
    expect(createFlow).not.toHaveBeenCalled();
    expect(params.setFlowNodes).not.toHaveBeenCalled();
    expect(params.resetRunView).not.toHaveBeenCalled();
  });

  it('内容不是 JSON 时报错', async () => {
    const createFlow = vi.fn();
    const { call } = importBridge('not json at all', createFlow);
    const { actions, params } = renderActions(call);

    expect(await actions.openFlow()).toBe(false);
    expect(params.pushToast).toHaveBeenCalledWith('error', '流程文件不是有效 JSON');
    expect(createFlow).not.toHaveBeenCalled();
  });

  it('有效流程文件正常导入并保存', async () => {
    const createFlow = vi.fn(async (payload) => ({ ok: true as const, data: { ...savedFlow, ...payload, flowId: 'imported' } }));
    const content = JSON.stringify(buildFlowDefinition(initialNodes, initialEdges, savedVariables, '导入的流程'));
    const { call } = importBridge(content, createFlow);
    const { actions, params } = renderActions(call);

    expect(await actions.openFlow()).toBe(true);
    expect(createFlow).toHaveBeenCalledWith(expect.objectContaining({ name: '导入的流程', status: 'draft' }));
    expect(params.setFlowNodes).toHaveBeenCalledOnce();
  });
});

describe('脚本生成与导出', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchFlowSnapshot).mockResolvedValue({ kind: 'ok', flow: savedFlow });
  });

  // 必须自身是泛型函数：callBridge 按 <T> 分配类型，签名固定成某个具体结果的
  // 函数赋不进去（内联 lambda 能过是因为它按上下文推断成泛型）。
  function bridgeReturning(result: unknown) {
    return async <T,>(action: (api: import('../types/electron').RpaBridge) => Promise<BridgeResult<T>>): Promise<T | null> => {
      const response = await action({
        exportScraplingScript: async () => ({ ok: true as const, data: result as T }),
        generateScraplingScript: async () => ({ ok: true as const, data: result as T }),
      } as unknown as import('../types/electron').RpaBridge);
      return response.ok ? response.data ?? null : null;
    };
  }

  it('离线模板不报「已生成」', async () => {
    // 离线模板一个页面都不抓，成功提示会让人拿着空脚本去跑。
    const { actions, params } = renderActions(bridgeReturning({
      content: '# offline', degraded: true, degradedReason: 'fetch failed', dependencies: [], filename: '门店合约抓取.py', language: 'python',
    }));

    await actions.generateScraplingScript();

    expect(params.setGeneratedScript).toHaveBeenCalledOnce();
    expect(params.pushToast).toHaveBeenCalledWith('error', expect.stringContaining('离线模板'));
  });

  it('正常生成的脚本报成功', async () => {
    const { actions, params } = renderActions(bridgeReturning({
      content: '# real', dependencies: [], filename: '门店合约抓取.py', language: 'python',
    }));

    await actions.generateScraplingScript();

    expect(params.pushToast).toHaveBeenCalledWith('success', '已生成 门店合约抓取.py');
  });

  it('导出脚本带上当前内容与文件名', async () => {
    const exportScript = vi.fn(async () => ({ ok: true, data: { canceled: false, name: '门店合约抓取.py' } }));
    const { actions, params } = renderActions(async (action) => {
      const result = await action({ exportScraplingScript: exportScript } as unknown as import('../types/electron').RpaBridge);
      return result.ok ? result.data ?? null : null;
    });

    await actions.exportScraplingScript('# content', '门店合约抓取.py');

    expect(exportScript).toHaveBeenCalledWith({ content: '# content', filename: '门店合约抓取.py' });
    expect(params.pushToast).toHaveBeenCalledWith('success', '已导出 门店合约抓取.py');
  });
  it('用户取消保存时不报导出成功', async () => {
    const { actions, params } = renderActions(bridgeReturning({ canceled: true }));

    await actions.exportScraplingScript('# content', '门店合约抓取.py');

    expect(params.pushToast).not.toHaveBeenCalled();
  });
});
