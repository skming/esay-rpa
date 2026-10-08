import type { Dispatch, SetStateAction } from 'react';
import type { Edge, Node } from '@xyflow/react';
import type { BridgeCallOptions, BridgeToast } from './electronBridgeTypes';
import { initialEdges, initialNodes } from '../data/studioData';
import { buildFlowDefinition, readFlowInputVariables, restoreFlowCanvas, serializeFlowDefinition } from '../lib/flowDefinition';
import { cloneFlowTemplate, type FlowTemplate } from '../lib/flowTemplates';
import { useFlowDraftStore } from '../stores/useFlowDraftStore';
import { usePropertyPanelStore } from '../stores/usePropertyPanelStore';
import { useAiChatStore } from '../stores/useAiChatStore';
import { useWorkspaceStore } from '../stores/useWorkspaceStore';
import { applyPendingDraftToNodes } from '../lib/pendingNodeDraft';
import { buildInitialFlowPayload, buildUpdatePayload, hasDefinitionChanged } from '../lib/flowVersioning';
import type { BridgeResult, FlowSnapshot, FlowVersionSnapshot, RpaBridge, BrowserExecutorKind } from '../types/electron';
import type { FlowCanvasSnapshot, RpaNodeData, RuntimeVariable } from '../types/rpa';
import { clearDraftStorage } from './useFlowDraftAutosave';
import { backend, fetchFlowSnapshot } from '../lib/backendClient';
import { toSafeFilename } from '../lib/filenames';
import type { UseElectronBridgeActionsParams, ElectronBridgeActions } from './electronBridgeActionTypes';


export function createFlowBridgeActions({
  activeRunId,
  activeRunFlowId,
  callBridge,
  clearLastRunOverrides,
  currentFlow,
  flowCanvas,
  flows,
  pushToast,
  dismissToast,
  inputVariables,
  resetRunView,
  restoreRecentRun,
  setCurrentFlow,
  setFlowEdges,
  setFlowNodes,
  setFlows,
  setInputVariables,
  setSelectedNodeId,
  setCanvasFitVersion,
}: Pick<
  UseElectronBridgeActionsParams,
  | 'activeRunId'
  | 'activeRunFlowId'
  | 'callBridge'
  | 'clearLastRunOverrides'
  | 'currentFlow'
  | 'flowCanvas'
  | 'flows'
  | 'pushToast'
  | 'dismissToast'
  | 'inputVariables'
  | 'resetRunView'
  | 'restoreRecentRun'
  | 'setCurrentFlow'
  | 'setFlowEdges'
  | 'setFlowNodes'
  | 'setFlows'
  | 'setInputVariables'
  | 'setSelectedNodeId'
  | 'setCanvasFitVersion'
>): Pick<
  ElectronBridgeActions,
  | 'applyFlowTemplate'
  | 'openFlow'
  | 'openFlowById'
  | 'silentlyRestoreCurrentFlow'
  | 'applyAiFlowUpdate'
  | 'loadFlowVersions'
  | 'rollbackFlowSnapshot'
  | 'loadFlows'
  | 'createNewFlow'
  | 'saveFlow'
  | 'exportFlow'
  | 'exportFlowById'
  | 'archiveCurrentFlow'
  | 'archiveFlowById'
  | 'duplicateFlowById'
  | 'moveFlowById'
  | 'setFlowStatusById'
  | 'deleteCurrentFlow'
  | 'deleteFlowById'
  | 'renameCurrentFlow'
  | 'setDefaultBrowserExecutor'
> {
  return {
    applyFlowTemplate: (template: FlowTemplate) => {
      const snapshot = cloneFlowTemplate(template);
      clearDraftStorage();
      resetRunView();
      setCurrentFlow(null);
      setFlowNodes(snapshot.nodes);
      setFlowEdges(snapshot.edges);
      setInputVariables(snapshot.variables);
      clearLastRunOverrides();
      if (typeof setSelectedNodeId === 'function') {
        setSelectedNodeId(snapshot.nodes.find((node) => node.id !== 'start' && node.id !== 'end')?.id ?? 'start');
      }
      pushToast('info', `已应用场景模板：${template.name}`);
    },
    openFlow: async (): Promise<boolean> => {
      const fileResult = await callBridge((api) => api.openFlow());
      if (fileResult === null || fileResult.canceled || fileResult.name === undefined) {
        return false;
      }
      const rawContent = typeof fileResult.content === 'string' ? fileResult.content.trim() : '';
      if (!rawContent) {
        pushToast('error', '文件内容为空');
        return false;
      }
      let definition: Record<string, unknown>;
      try {
        definition = JSON.parse(rawContent) as Record<string, unknown>;
      } catch {
        pushToast('error', '流程文件不是有效 JSON');
        return false;
      }
      // JSON 合法不等于是流程文件：不校验的话任意 JSON（如 package.json）也会被存成一个空流程。
      // 复用画布还原逻辑判定——还原不出任何节点即判为非流程文件，避免再立一套结构白名单。
      if (restoreFlowCanvas(definition) === null) {
        pushToast('error', '该文件不是有效的流程文件');
        return false;
      }

      // 清除旧草稿，避免页面重新挂载时恢复之前的流程
      clearDraftStorage();
      resetRunView();

      applyFlowDefinitionToCanvas(definition, setFlowNodes, setFlowEdges);
      const parsedVars = readFlowInputVariables(definition);
      setInputVariables(parsedVars);
      clearLastRunOverrides();

      const flowName = typeof definition.name === 'string' && definition.name.trim()
        ? definition.name.trim()
        : fileResult.name.replace(/\.rpa\.json$/i, '').replace(/\.json$/i, '') || '导入流程';
      const flowVersion = typeof definition.version === 'string' ? definition.version : 'v1.0.0';

      const loadingToastId = pushToast('info', '正在导入流程...');
      const saved = await callBridge(
        (api) =>
          api.createFlow({
            definition,
            folderPath: '默认目录',
            inputVariables: parsedVars,
            name: flowName,
            status: 'draft',
            version: flowVersion,
          }),
        undefined,
        { silent: true }
      );
      dismissToast(loadingToastId);
      if (saved !== null) {
        setCurrentFlow(saved);
        setFlows((current) => [saved, ...current.filter((f) => f.flowId !== saved.flowId)]);
        pushToast('success', `已导入并保存「${flowName}」`);
      } else {
        // 后端不可用时仍应用到画布，不阻断使用
        setCurrentFlow(null);
        pushToast('info', `已打开 ${fileResult.name}（未连接后端，仅本地使用）`);
      }
      return true;
    },
    openFlowById: async (flowId: string) => {
      // 直接取单个流程，避免全量 listFlows 往返及其偶发的 500 错误
      const fetched = await fetchFlowSnapshot(flowId);
      if (fetched.kind === 'missing') {
        forgetDeletedFlow(flowId);
        setFlows((current) => current.filter((flow) => flow.flowId !== flowId));
        pushToast('error', '该流程已被删除');
        return;
      }

      let target: FlowSnapshot | null = fetched.kind === 'ok' ? fetched.flow : null;
      if (target === null) {
        const flows = await callBridge((api) => api.listFlows());
        if (flows === null) return;
        setFlows(flows);
        target = flows.find((flow) => flow.flowId === flowId) ?? null;
        if (target === null) {
          pushToast('error', '未找到指定流程版本');
          return;
        }
      } else {
        setFlows(upsertFlow(target));
      }

      const preserveActiveRun = activeRunId !== null && activeRunFlowId === flowId;
      if (!preserveActiveRun) {
        resetRunView();
      }
      clearDraftStorage();
      openFlowSnapshot(target, { pushToast, setCurrentFlow, setFlowEdges, setFlowNodes, setInputVariables });
      if (!preserveActiveRun) await restoreRecentRun(flowId);
    },
    silentlyRestoreCurrentFlow: async (flowId: string, options?: { restoreCanvas?: boolean }) => {
      const fetched = await fetchFlowSnapshot(flowId);
      if (fetched.kind === 'missing') {
        // 流程已被删除：不清掉引用的话，每次启动都会再对同一个 id 发一次注定 404 的请求
        forgetDeletedFlow(flowId);
        pushToast('info', '上次打开的流程已被删除');
        return;
      }
      if (fetched.kind !== 'ok') return;
      const flow = fetched.flow;
      setCurrentFlow(flow);
      if (options?.restoreCanvas === true) {
        applyFlowDefinitionToCanvas(flow.definition, setFlowNodes, setFlowEdges);
        setInputVariables(flow.inputVariables);
        setCanvasFitVersion((v) => v + 1);
      }
      await restoreRecentRun(flowId);
    },
    applyAiFlowUpdate: async (flowId: string) => {
      const fetched = await fetchFlowSnapshot(flowId);
      if (fetched.kind !== 'ok') return; // 取不到时画布保持原状
      const flow = fetched.flow;
      // 故意不调用 setInputVariables：AI 工具只改节点/边，调用它会清掉用户未保存的本地变量编辑
      setCurrentFlow(flow);
      setFlows(upsertFlow(flow));
      applyFlowDefinitionToCanvas(flow.definition, setFlowNodes, setFlowEdges);
    },
    loadFlowVersions: async () => {
      if (currentFlow === null) return [];
      return await backend.listFlowVersions(currentFlow.flowId);
    },
    rollbackFlowSnapshot: async (snapshot: FlowVersionSnapshot) => {
      if (currentFlow === null) return false;
      const currentVariables = new Map(currentFlow.inputVariables.map((variable) => [variable.name, variable]));
      const restoredVariables = snapshot.inputVariables.map((variable) => {
        if (variable.category !== 'credential' && variable.sensitive !== true) return variable;
        const current = currentVariables.get(variable.name);
        return current === undefined ? variable : { ...variable, value: current.value };
      });
      const updated = await callBridge((api) => api.updateFlow(currentFlow.flowId, {
        acceptanceContract: snapshot.acceptanceContract,
        description: snapshot.description ?? undefined,
        definition: snapshot.definition,
        inputVariables: restoredVariables,
      }));
      if (updated === null) return false;
      clearDraftStorage();
      resetRunView();
      setCurrentFlow(updated);
      clearLastRunOverrides();
      setFlows((prev) => [updated, ...prev.filter((f) => f.flowId !== updated.flowId)]);
      applyFlowDefinitionToCanvas(updated.definition, setFlowNodes, setFlowEdges);
      setInputVariables(updated.inputVariables);
      pushToast('success', `已恢复 revision ${snapshot.revision ?? snapshot.version}，并保存为新版本`);
      return true;
    },
    loadFlows: async (options?: BridgeCallOptions) => {
      const flows = await callBridge((api) => api.listFlows(), undefined, options);
      if (flows !== null) {
        setFlows(flows);
      }
    },
    createNewFlow: async (name?: string) => {
      const flowName = typeof name === 'string' && name.trim() ? name.trim() : '新建 RPA 流程';
      const created = await callBridge((api) => api.createFlow({
        ...buildInitialFlowPayload(buildFlowDefinition(initialNodes, initialEdges, [], flowName), [], flowName),
        status: 'draft',
      }));
      if (created === null) return false;
      clearDraftStorage();
      resetRunView();
      setCurrentFlow(created);
      setFlows((current) => [created, ...current.filter((flow) => flow.flowId !== created.flowId)]);
      useWorkspaceStore.getState().setLastOpenedFlowId(created.flowId);
      setFlowNodes(restoreInitialNodes());
      setFlowEdges(restoreInitialEdges());
      setInputVariables([]);
      clearLastRunOverrides();
      pushToast('info', `已创建草稿：${flowName}`);
      return true;
    },
    saveFlow: async () => {
      const flow = await persistCurrentFlow({ callBridge, currentFlow, flows, flowCanvas, inputVariables });
      if (flow !== null) {
        setCurrentFlow(flow);
        setFlows((current) => [flow, ...current.filter((item) => item.flowId !== flow.flowId)]);
        pushToast('success', `已保存流程 ${flow.name}`);
      }
    },
    exportFlow: async () => {
      const suggestedName = `${toSafeFilename(currentFlow?.name)}.rpa.json`;
      const fileResult = await callBridge((api) =>
        api.saveFlow({ suggestedName, content: serializeFlowDefinition(flowCanvas.nodes, flowCanvas.edges, inputVariables, currentFlow?.name) })
      );
      if (fileResult !== null && !fileResult.canceled && fileResult.name !== undefined) {
        pushToast('success', `已导出 ${fileResult.name}`);
      }
    },
    exportFlowById: async (flowId: string) => {
      // 优先从已加载列表取，避免 open flow（会替换 currentFlow/画布状态）及 stale closure
      const cached = flows.find((f) => f.flowId === flowId);
      const fetched = cached === undefined ? await fetchFlowSnapshot(flowId) : null;
      if (fetched?.kind === 'missing') {
        forgetDeletedFlow(flowId);
        setFlows((current) => current.filter((f) => f.flowId !== flowId));
      }
      const flow = cached ?? (fetched?.kind === 'ok' ? fetched.flow : null);
      if (flow === null) {
        pushToast('error', '未找到指定流程');
        return;
      }
      const suggestedName = `${toSafeFilename(flow.name)}.rpa.json`;
      const content = JSON.stringify(flow.definition, null, 2);
      const fileResult = await callBridge((api) => api.saveFlow({ suggestedName, content }));
      if (fileResult !== null && !fileResult.canceled && fileResult.name !== undefined) {
        pushToast('success', `已导出 ${fileResult.name}`);
      }
    },
    archiveCurrentFlow: async () => {
      if (currentFlow === null) {
        pushToast('error', '当前流程尚未保存，无法归档');
        return;
      }
      const archived = await callBridge((api) => api.archiveFlow(currentFlow.flowId));
      if (archived !== null) {
        setCurrentFlow(archived);
        setFlows((current) => [archived, ...current.filter((item) => item.flowId !== archived.flowId)]);
        pushToast('success', `已归档 ${archived.name}`);
      }
    },
    archiveFlowById: async (flowId: string) => {
      const archived = await callBridge((api) => api.archiveFlow(flowId));
      if (archived !== null) {
        setFlows((current) => [archived, ...current.filter((item) => item.flowId !== archived.flowId)]);
        if (currentFlow?.flowId === archived.flowId) {
          setCurrentFlow(archived);
        }
        pushToast('success', `已归档 ${archived.name}`);
      }
    },
    duplicateFlowById: async (flowId: string) => {
      const copy = await callBridge((api) => api.duplicateFlow(flowId), '已创建副本');
      if (copy !== null) {
        setFlows((current) => [copy, ...current]);
      }
    },
    moveFlowById: async (flowId: string, folderPath: string) => {
      const moved = await callBridge((api) => api.moveFlow(flowId, folderPath), `已移动到 ${folderPath}`);
      if (moved !== null) {
        setFlows((current) => current.map((item) => (item.flowId === flowId ? moved : item)));
        if (currentFlow?.flowId === flowId) {
          setCurrentFlow(moved);
        }
      }
    },
    setFlowStatusById: async (flowId: string, status: import('../types/electron').FlowStatus) => {
      const updated = await callBridge((api) => api.setFlowStatus(flowId, status));
      if (updated !== null) {
        setFlows((current) => current.map((item) => (item.flowId === flowId ? updated : item)));
        if (currentFlow?.flowId === flowId) {
          setCurrentFlow(updated);
        }
        pushToast('success', `流程状态已更新`);
      }
    },
    deleteCurrentFlow: async () => {
      if (currentFlow === null) {
        pushToast('error', '当前流程尚未保存，无法删除');
        return;
      }
      const flowId = currentFlow.flowId;
      const result = await callBridge((api) => api.deleteFlow(flowId));
      if (result !== null && result.deleted) {
        resetRunView();
        setCurrentFlow(null);
        setFlows((current) => current.filter((item) => item.flowId !== flowId));
        forgetDeletedFlow(flowId);
        pushToast('success', '流程版本已删除');
      }
    },
    deleteFlowById: async (flowId: string) => {
      const result = await callBridge((api) => api.deleteFlow(flowId));
      if (result !== null && result.deleted) {
        if (currentFlow?.flowId === flowId) {
          resetRunView();
          setCurrentFlow(null);
        }
        setFlows((current) => current.filter((item) => item.flowId !== flowId));
        forgetDeletedFlow(flowId);
        pushToast('success', '流程版本已删除');
      }
    },
    renameCurrentFlow: async (name: string) => {
      const trimmed = name.trim();
      if (!trimmed) return;
      if (currentFlow === null) {
        // 未保存的本地草稿，仅内存中改名
        setCurrentFlow((prev) => prev ? { ...prev, name: trimmed } : prev);
        return;
      }
      const renamed = { ...currentFlow, name: trimmed };
      const flow = await persistCurrentFlow({ callBridge, currentFlow: renamed, flows, flowCanvas, inputVariables });
      if (flow !== null) {
        setCurrentFlow(flow);
        setFlows((current) => [flow, ...current.filter((f) => f.flowId !== flow.flowId)]);
        pushToast('success', `已重命名为「${flow.name}」`);
      }
    },
    setDefaultBrowserExecutor: async (browserExecutor: BrowserExecutorKind) => {
      if (currentFlow === null || currentFlow.flowId.startsWith('local-')) {
        setCurrentFlow((prev) => (prev ? { ...prev, defaultBrowserExecutor: browserExecutor } : prev));
        return;
      }
      const flow = await callBridge((api) => api.updateFlow(currentFlow.flowId, { defaultBrowserExecutor: browserExecutor }));
      if (flow !== null) {
        setCurrentFlow(flow);
        setFlows((current) => current.map((item) => (item.flowId === flow.flowId ? flow : item)));
      }
    }
  };
}

/** 有则替换、无则置顶插入。后端返回的快照总是比列表里的新，直接覆盖。 */
function upsertFlow(flow: FlowSnapshot): (prev: FlowSnapshot[]) => FlowSnapshot[] {
  return (prev) => {
    const idx = prev.findIndex((f) => f.flowId === flow.flowId);
    if (idx < 0) return [flow, ...prev];
    const next = [...prev];
    next[idx] = flow;
    return next;
  };
}

/**
 * 抹掉所有指向该流程的持久化引用。删除流程后不做这一步，下次启动会拿着已不存在的 id
 * 去恢复"上次打开的流程"，每次都换来一个 404。
 */
function forgetDeletedFlow(flowId: string): void {
  const workspace = useWorkspaceStore.getState();
  if (workspace.lastOpenedFlowId === flowId) {
    workspace.setLastOpenedFlowId(null);
  }
  useFlowDraftStore.getState().detachFlowId(flowId);
}

/**
 * 草稿拿到真实 flowId 时把 AI 对话一起搬过去。会话 key 由 flowId 派生（见 useAiChat 的
 * sessionKey），保存动作换了 id 就等于换了会话，用户会看到刚才那轮对话凭空消失——第一轮被
 * 站点拦截、还没生成节点时尤其致命：那轮记录的正是"为什么没做成"。
 * 搬迁只在这里做，不放在 AI 面板里按 key 变化猜：从草稿切到另一个已存在的流程同样会换 key，
 * 靠猜就会把草稿对话挂到别人的流程上。
 */
async function carryOverAiChat(fromFlowId: string | null, toFlowId: string): Promise<void> {
  const fromKey = fromFlowId === null ? 'local' : `flow_${fromFlowId}`;
  const toKey = `flow_${toFlowId}`;
  useAiChatStore.getState().migrateSession(fromKey, toKey);
  try {
    await backend.renameAiChat(fromKey, toKey);
  } catch { /* 落盘搬迁失败不影响保存本身：内存里的会话已经挂到新 key 上了 */ }
}

export async function persistCurrentFlow({
  callBridge,
  currentFlow,
  flows,
  flowCanvas,
  inputVariables
}: {
  callBridge: <T>(action: (bridge: RpaBridge) => Promise<BridgeResult<T>>, successMessage?: string, options?: BridgeCallOptions) => Promise<T | null>;
  currentFlow: FlowSnapshot | null;
  flows: FlowSnapshot[];
  flowCanvas: FlowCanvasSnapshot;
  inputVariables: RuntimeVariable[];
}): Promise<FlowSnapshot | null> {
  const definition = buildFlowDefinition(flowCanvas.nodes, flowCanvas.edges, inputVariables, currentFlow?.name);
  if (currentFlow === null) {
    const created = await callBridge((api) => api.createFlow(buildInitialFlowPayload(definition, inputVariables)));
    if (created !== null) await carryOverAiChat(null, created.flowId);
    return created;
  }
  if (currentFlow.flowId.startsWith('local-')) {
    const created = await callBridge((api) => api.createFlow(buildInitialFlowPayload(definition, inputVariables, currentFlow.name)));
    if (created !== null) await carryOverAiChat(currentFlow.flowId, created.flowId);
    return created;
  }
  const savedFlow = flows.find((flow) => flow.flowId === currentFlow.flowId);
  const nameChanged = savedFlow !== undefined && savedFlow.name !== currentFlow.name;
  // 无变化且已是 active 状态时跳过写入，避免每次保存都产生新的版本快照
  if (!nameChanged && !hasDefinitionChanged(currentFlow, definition, inputVariables) && currentFlow.status === 'active') {
    return currentFlow;
  }
  return await callBridge((api) => api.updateFlow(currentFlow.flowId, buildUpdatePayload(currentFlow, flows, definition, inputVariables)));
}

function applyFlowDefinitionToCanvas(
  definition: Record<string, unknown>,
  setFlowNodes: Dispatch<SetStateAction<Node<RpaNodeData>[]>>,
  setFlowEdges: Dispatch<SetStateAction<Edge[]>>
): void {
  const restored = restoreFlowCanvas(definition);
  if (restored === null) {
    return;
  }
  const nodes = ensureStartEndNodes(restored.nodes);
  setFlowNodes(nodes);
  setFlowEdges(restored.edges);
}

function ensureStartEndNodes(nodes: Node<RpaNodeData>[]): Node<RpaNodeData>[] {
  const hasStart = nodes.some((n) => n.id === 'start');
  const hasEnd = nodes.some((n) => n.id === 'end');
  if (hasStart && hasEnd) {
    return nodes;
  }
  const result = [...nodes];
  if (!hasStart) {
    result.unshift({ ...initialNodes[0], position: { ...initialNodes[0].position }, data: { ...initialNodes[0].data } });
  }
  if (!hasEnd) {
    const lastNode = result[result.length - 1];
    const endY = lastNode !== undefined ? lastNode.position.y + 120 : initialNodes[1].position.y;
    result.push({ ...initialNodes[1], position: { x: initialNodes[1].position.x, y: endY }, data: { ...initialNodes[1].data } });
  }
  return result;
}

function openFlowSnapshot(
  flow: FlowSnapshot,
  {
    pushToast,
    setCurrentFlow,
    setFlowEdges,
    setFlowNodes,
    setInputVariables
  }: {
    pushToast: (type: BridgeToast['type'], message: string, icon?: string) => void;
    setCurrentFlow: Dispatch<SetStateAction<FlowSnapshot | null>>;
    setFlowEdges: Dispatch<SetStateAction<Edge[]>>;
    setFlowNodes: Dispatch<SetStateAction<Node<RpaNodeData>[]>>;
    setInputVariables: (variables: RuntimeVariable[]) => void;
  }
): void {
  setCurrentFlow(flow);
  applyFlowDefinitionToCanvas(flow.definition, setFlowNodes, setFlowEdges);
  setInputVariables(flow.inputVariables);
  pushToast('success', `已打开 ${flow.name} ${flow.version}`);
}

function restoreInitialNodes(): Node<RpaNodeData>[] {
  return initialNodes.map((node) => ({
    ...node,
    data: { ...node.data, action: node.data.action === undefined ? undefined : { ...node.data.action } },
    position: { ...node.position }
  }));
}

function restoreInitialEdges(): Edge[] {
  return initialEdges.map((edge) => ({
    ...edge
  }));
}

/** 提交未保存的节点草稿并返回新快照，避免本次运行读取 React 状态更新前的旧节点。 */
export function commitPendingNodeDraft(
  flowCanvas: FlowCanvasSnapshot,
  setFlowNodes: Dispatch<SetStateAction<Node<RpaNodeData>[]>>
): FlowCanvasSnapshot {
  const { pendingDraft, setPendingDraft } = usePropertyPanelStore.getState();
  const nodes = applyPendingDraftToNodes(flowCanvas.nodes, pendingDraft);
  if (nodes === flowCanvas.nodes) {
    return flowCanvas;
  }
  setFlowNodes((current) => applyPendingDraftToNodes(current, pendingDraft));
  setPendingDraft(null);
  return { ...flowCanvas, nodes };
}
