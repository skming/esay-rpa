import type { BridgeCallOptions } from './electronBridgeTypes';
import { buildFlowDefinition } from '../lib/flowDefinition';
import { buildRuntimeVariablePayload } from '../lib/inputVariables';
import { getBlockingRunIssue, validateRunConfiguration } from '../lib/runValidation';
import { useBottomPanelStore } from '../stores/useBottomPanelStore';
import type { FlowCanvasSnapshot, RuntimeVariable } from '../types/rpa';
import { normalizeRunConcurrency } from '../lib/runConfigPresentation';
import type { UseElectronBridgeActionsParams, ElectronBridgeActions, StartRunOptions } from './electronBridgeActionTypes';
import { commitPendingNodeDraft } from './flowBridgeActions';

export function createRunBridgeActions({
  activeFlowNameRef,
  activeRunId,
  callBridge,
  currentFlow,
  flowCanvas,
  flows,
  pushToast,
  inputVariables,
  resetRunView,
  setLastRunOverrides,
  setFlowNodes,
  setSelectedNodeId,
  setActiveRunId,
  setActiveRunFlowId,
  setArtifactContent,
  setArtifacts,
  setGeneratedScript,
  setQueueStats,
  setRuntimeStatus,
  setRuns,
  setSiteAnalysis,
  setVariables,
  setLogs,
  setConfirmationMessage,
}: Pick<
  UseElectronBridgeActionsParams,
  | 'activeFlowNameRef'
  | 'activeRunId'
  | 'callBridge'
  | 'currentFlow'
  | 'flowCanvas'
  | 'flows'
  | 'pushToast'
  | 'inputVariables'
  | 'resetRunView'
  | 'setLastRunOverrides'
  | 'setFlowNodes'
  | 'setSelectedNodeId'
  | 'setActiveRunId'
  | 'setActiveRunFlowId'
  | 'setArtifactContent'
  | 'setArtifacts'
  | 'setGeneratedScript'
  | 'setQueueStats'
  | 'setRuntimeStatus'
  | 'setRuns'
  | 'setSiteAnalysis'
  | 'setVariables'
  | 'setLogs'
  | 'setConfirmationMessage'
>): Pick<
  ElectronBridgeActions,
  | 'startRun'
  | 'stopRun'
  | 'resumeConfirmation'
  | 'generateScraplingScript'
  | 'exportScraplingScript'
  | 'analyzeCurrentSite'
  | 'loadRuns'
  | 'loadFlowRuns'
  | 'loadTaskVariables'
  | 'loadArtifacts'
  | 'readArtifact'
  | 'loadQueueStats'
> {
  return {
    startRun: async (options = 'run') => {
      const runOptions: StartRunOptions = typeof options === 'string' ? { mode: options } : options;
      const mode = runOptions.mode ?? 'run';

      // 指定了已保存流程 ID 时（如来自任务中心）跳过画布校验，直接让后端运行已保存的定义
      const savedFlowId = typeof runOptions.flowId === 'string' && runOptions.flowId.length > 0
        ? runOptions.flowId
        : null;
      const isRemoteRun = savedFlowId !== null && savedFlowId !== currentFlow?.flowId;

      // 远程运行分支：目标流程不是当前打开的画布，直接让后端按已保存定义启动，
      // 不做画布校验/不拼 flowDefinition——本地画布可能是另一个流程，不能混用
      if (isRemoteRun) {
        const savedFlow = flows.find((f) => f.flowId === savedFlowId);
        const flowName = savedFlow?.name ?? '未命名流程';
        const browserExecutor = runOptions.browserExecutor ?? savedFlow?.defaultBrowserExecutor ?? 'playwright';
        activeFlowNameRef.current = flowName;
        resetRunView();
        setActiveRunFlowId(savedFlowId);
        setRuntimeStatus('running');
        const result = await callBridge((api) =>
          api.startRun({
            mode,
            adaptive: true,
            autoSave: true,
            browserExecutor,
            concurrency: normalizeRunConcurrency(runOptions.concurrency),
            failureStrategy: runOptions.failureStrategy ?? 'stop',
            flowId: savedFlowId,
            flowName,
            scope: runOptions.scope ?? 'full',
            screenshot: runOptions.screenshot ?? true,
            selector: '',
            startNodeId: runOptions.startNodeId,
            targetUrl: '',
            timeoutMs: runOptions.timeoutMs ?? 30_000,
          })
        );
        if (result !== null) {
          setActiveRunId(result.runId);
          setActiveRunFlowId(savedFlowId);
          setRuntimeStatus(result.status);
          pushToast('info', `「${flowName}」已提交运行`);
        } else {
          setActiveRunFlowId(null);
          setRuntimeStatus('ready');
        }
        return;
      }

      const canvas = commitPendingNodeDraft(flowCanvas, setFlowNodes);
      const executableNode = findExecutableFetchNode(canvas);
      const flowDefinition = buildFlowDefinition(canvas.nodes, canvas.edges, inputVariables, currentFlow?.name);
      const runVariables = mergeRunVariables(inputVariables, runOptions.overrideVariables ?? []);
      const scope = runOptions.scope ?? 'full';
      const validation = validateRunConfiguration(canvas.nodes, canvas.edges, {
        availableVariableNames: runVariables.map((variable) => variable.name),
        scope,
        startNodeId: runOptions.startNodeId
      });
      let variablesPayload: Record<string, unknown> = {};
      let overridePayload: Record<string, unknown> = {};
      let variableError: string | null = null;
      try {
        variablesPayload = buildRuntimeVariablePayload(runVariables);
        overridePayload = buildRuntimeVariablePayload(runOptions.overrideVariables ?? []);
      } catch (error) {
        variableError = error instanceof Error ? error.message : '输入变量不合法';
      }
      const blockingIssue = variableError === null ? getBlockingRunIssue(validation) : { nodeId: 'start', message: variableError };
      if (blockingIssue !== null) {
        resetRunView();
        if (typeof setSelectedNodeId === 'function') {
          setSelectedNodeId(blockingIssue.nodeId);
        }
        // 注入错误日志条目，使错误在底部面板的错误 tab 中可见（而不仅是 toast）
        const now = new Date();
        const timeStr = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
        setLogs([{
          id: `validation-${Date.now()}`,
          time: timeStr,
          level: 'error',
          message: blockingIssue.message,
          nodeId: blockingIssue.nodeId,
        }]);
        useBottomPanelStore.getState().setActiveTab('errors');
        useBottomPanelStore.getState().setOpen(true);
        return;
      }
      if (validation.primaryIssue?.severity === 'warn') {
        pushToast('info', validation.primaryIssue.message);
      }
      // 乐观反馈：点击运行后立即进入"运行中"状态，无需等待后端
      const flowName = currentFlow?.name ?? '未命名流程';
      const browserExecutor = runOptions.browserExecutor ?? currentFlow?.defaultBrowserExecutor ?? 'playwright';
      activeFlowNameRef.current = flowName;
      resetRunView();
      setActiveRunFlowId(currentFlow?.flowId ?? null);
      setRuntimeStatus('running');
      const result = await callBridge((api) =>
        api.startRun({
          mode,
          adaptive: true,
          autoSave: true,
          browserExecutor,
          concurrency: normalizeRunConcurrency(runOptions.concurrency),
          failureStrategy: runOptions.failureStrategy ?? 'stop',
          flowDefinition,
          flowId: currentFlow?.flowId ?? undefined,
          flowName,
          scope,
          selector: executableNode.selector ?? '',
          screenshot: runOptions.screenshot ?? true,
          startNodeId: runOptions.startNodeId,
          targetUrl: executableNode.targetUrl ?? '',
          timeoutMs: executableNode.timeoutMs ?? runOptions.timeoutMs ?? 30_000,
          overrideVariables: overridePayload,
          variables: variablesPayload
        })
      );
      if (result !== null) {
        setLastRunOverrides(currentFlow?.flowId ?? null, runOptions.overrideVariables ?? []);
        setActiveRunId(result.runId);
        setActiveRunFlowId(currentFlow?.flowId ?? null);
        setRuntimeStatus(result.status);
        pushToast('info', getRunStartedMessage(runOptions));
      } else {
        setActiveRunFlowId(null);
        setRuntimeStatus('ready');
      }
    },
    stopRun: async () => {
      const result = await callBridge((api) => api.stopRun(activeRunId ?? undefined));
      if (result !== null) {
        setActiveRunId(null);
        setActiveRunFlowId(null);
        setRuntimeStatus(result.status);
        if (result.stopped) {
          pushToast('info', '流程已停止');
        }
      }
    },
    resumeConfirmation: async () => {
      if (activeRunId === null) return;
      const result = await callBridge((api) => api.resumeConfirmation(activeRunId));
      if (result !== null) setConfirmationMessage(null);
    },
    generateScraplingScript: async () => {
      const flowDefinition = buildFlowDefinition(flowCanvas.nodes, flowCanvas.edges, inputVariables, currentFlow?.name ?? '未命名流程');
      const result = await callBridge((api) => api.generateScraplingScript({ flowDefinition, flowName: currentFlow?.name ?? '未命名流程' }));
      if (result !== null) {
        setGeneratedScript(result);
        // 后端不可用时拿到的是离线模板，它一个页面都不抓：报「已生成」会让人拿着空脚本去跑。
        if (result.degraded === true) {
          pushToast('error', `后端不可用，已生成离线模板 ${result.filename}（不含抓取逻辑）`);
        } else {
          pushToast('success', `已生成 ${result.filename}`);
        }
      }
    },
    exportScraplingScript: async (content: string, filename: string) => {
      const result = await callBridge((api) => api.exportScraplingScript({ content, filename }));
      if (result !== null && !result.canceled && result.name !== undefined) {
        pushToast('success', `已导出 ${result.name}`);
      }
    },
    analyzeCurrentSite: async () => {
      const scriptTarget = readBrowserScriptTarget(flowCanvas, { actionLabel: '站点分析', requireSelector: false });
      if (scriptTarget.error !== null) {
        pushToast('info', scriptTarget.error);
        return;
      }
      const { selector, targetUrl } = scriptTarget;
      const result = await callBridge((api) => api.analyzeSite({ maxCandidates: 8, selector: selector || undefined, targetUrl }), '站点分析已完成');
      if (result !== null) {
        setSiteAnalysis(result);
      }
    },
    loadRuns: async (options = {}) => {
      const { silent, ...query } = options;
      const result = await callBridge((api) => api.listRuns(query), undefined, { silent });
      if (result !== null) {
        setRuns(result);
      }
    },
    loadFlowRuns: async (flowId: string, options = {}) => {
      const { silent, ...query } = options;
      const result = await callBridge((api) => api.listFlowRuns(flowId, query), undefined, { silent });
      if (result !== null) {
        setRuns(result);
      }
    },
    loadTaskVariables: async (taskId: string) => {
      const result = await callBridge((api) => api.listTaskVariables(taskId));
      if (result !== null) {
        setVariables(result);
      }
    },
    loadArtifacts: async (taskId: string) => {
      const result = await callBridge((api) => api.listArtifacts(taskId));
      if (result !== null) {
        setArtifacts(result);
      }
    },
    readArtifact: async (taskId: string, artifactId: string) => {
      const result = await callBridge((api) => api.readArtifact(taskId, artifactId));
      if (result !== null) {
        setArtifactContent(result);
        pushToast('success', `已读取 ${result.artifact.filename}`);
      }
    },
    loadQueueStats: async (options?: BridgeCallOptions) => {
      const result = await callBridge((api) => api.getQueueStats(), undefined, options);
      if (result !== null) {
        setQueueStats(result);
      }
    }
  };
}

type BrowserScriptTarget =
  | { error: null; selector: string; targetUrl: string }
  | { error: string; selector?: never; targetUrl?: never };

function findExecutableFetchNode(flowCanvas: FlowCanvasSnapshot): { selector?: string; targetUrl?: string; timeoutMs?: number } {
  for (const node of flowCanvas.nodes) {
    if (node.data.action?.type !== 'browser.fetch') {
      continue;
    }
    return {
      selector: node.data.action.selector,
      targetUrl: node.data.action.targetUrl,
      timeoutMs: node.data.action.timeoutMs
    };
  }
  return {};
}

function readBrowserScriptTarget(
  flowCanvas: FlowCanvasSnapshot,
  options: { actionLabel: string; requireSelector: boolean }
): BrowserScriptTarget {
  const openNode = flowCanvas.nodes.find((node) => node.data.action?.type === 'browser.open' || node.data.action?.type === 'browser.tab.open');
  const fetchNode = flowCanvas.nodes.find((node) => node.data.action?.type === 'browser.fetch' || node.data.action?.type === 'browser.extract');
  const flowUrl = openNode?.data.action?.targetUrl ?? openNode?.data.action?.url;
  const flowSelector = fetchNode?.data.action?.selector;
  const targetUrl = normalizeNonEmptyString(flowUrl);
  const selector = normalizeNonEmptyString(flowSelector);

  if (!isHttpUrl(targetUrl)) {
    return {
      error: targetUrl.includes('${')
        ? `${options.actionLabel}需要具体网址，请先用浏览器拾取器捕获当前页面，或把打开网页节点改为真实 URL`
        : `${options.actionLabel}前需要先配置打开网页节点的 HTTP URL`
    };
  }
  if (options.requireSelector && selector === '') {
    return { error: `${options.actionLabel}前需要先配置抓取/提取节点选择器，或使用拾取器选择页面元素` };
  }
  return { error: null, selector, targetUrl };
}

function normalizeNonEmptyString(value: unknown): string {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : '';
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

function mergeRunVariables(baseVariables: RuntimeVariable[], overrideVariables: RuntimeVariable[]): RuntimeVariable[] {
  const merged = new Map(baseVariables.map((variable) => [variable.name, variable]));
  for (const variable of overrideVariables) {
    merged.set(variable.name, variable);
  }
  return [...merged.values()];
}

function getRunStartedMessage(options: StartRunOptions): string {
  if (options.mode === 'debug') {
    return '调试运行已启动';
  }
  if (options.scope === 'from-selection') {
    return '已从选中步骤启动运行';
  }
  if (options.scope === 'selected-only') {
    return '已启动选中步骤运行';
  }
  return '流程运行已启动';
}
