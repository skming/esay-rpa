const { BackendClient } = require('./backendClient.cjs');
const { IPC_CHANNELS } = require('./ipcChannels.cjs');

const runtimeNodeIds = ['start', 'n1', 'n2', 'n3', 'n4', 'n5', 'n6', 'n7', 'n8', 'n9', 'n10', 'n11', 'end'];

const nodeLogMessages = {
  start: ['info', '流程启动 · 初始化执行上下文'],
  n1: ['running', '执行节点 n1'],
  n2: ['running', '执行节点 n2'],
  n3: ['running', '执行节点 n3'],
  n4: ['running', '执行节点 n4'],
  n5: ['info', '执行节点 n5'],
  n6: ['running', '执行节点 n6'],
  n7: ['success', '节点执行完成 · n7'],
  n8: ['running', '执行节点 n8'],
  n9: ['info', '已跳过节点 n9 · 条件未命中'],
  n10: ['success', '节点执行完成 · n10'],
  n11: ['success', '节点执行完成 · n11'],
  end: ['success', '流程执行完成 · 状态 success']
};

const nodeBadges = {
  n6: '循环',
  n9: '跳过'
};

const runScopeLabels = {
  full: '完整运行',
  'from-selection': '从选中步骤运行',
  'selected-only': '仅运行选中步骤'
};

const failureStrategyLabels = {
  stop: '停止运行',
  continue: '继续执行',
  retry: '重试当前步骤'
};

let activeRun = null;

function createRuntimeController({ backendClient = new BackendClient(), sendEvent } = {}) {
  function emit(win, event) {
    if (win !== null && !win.isDestroyed()) {
      win.webContents.send(IPC_CHANNELS.run.event, event);
    }
  }

  function stopActiveRun(status, message) {
    if (activeRun === null) {
      return { stopped: false, status: 'ready' };
    }

    const { runId, timers, win } = activeRun;
    timers.forEach((timer) => clearTimeout(timer));
    if (activeRun.logSocket !== undefined) {
      activeRun.logSocket.close();
    }
    activeRun = null;
    emit(win, {
      type: 'run:finish',
      payload: {
        runId,
        status,
        finishedAt: new Date().toISOString(),
        message
      }
    });
    return { stopped: true, runId, status };
  }

  function schedule(win, runId, callback, delayMs) {
    const timer = setTimeout(() => {
      if (activeRun?.runId !== runId) {
        return;
      }
      callback();
    }, delayMs);
    activeRun?.timers.push(timer);
  }

  async function startRun(win, payload = {}) {
    if (win === null) {
      throw new Error('Window is unavailable.');
    }

    if (activeRun !== null) {
      stopActiveRun('stopped', '上一次运行已被新的运行请求停止');
    }

    if (payload.backend !== 'mock') {
      try {
        return await startBackendRun(win, payload);
      } catch (error) {
        return startMockRun(win, payload, `后端不可用，切换到本地模拟运行 · ${error.message}`);
      }
    }

    return startMockRun(win, payload);
  }

  function startMockRun(win, payload = {}, fallbackReason) {
    if (win === null) {
      throw new Error('Window is unavailable.');
    }

    const runId = `run-${Date.now()}`;
    const startedAt = Date.now();
    activeRun = { runId, timers: [], win };
    const startPayload = {
      runId,
      flowId: payload.flowId ?? null,
      status: 'running',
      totalSteps: runtimeNodeIds.length,
      startedAt: new Date(startedAt).toISOString(),
      flowName: payload.flowName ?? '未命名流程',
    };

    emit(win, { type: 'run:start', payload: startPayload });
    emitRunConfigState(win, runId, payload);
    emitLog(win, runId, 'info', `流程启动 · ${payload.flowName ?? '未命名流程'} · ${payload.mode === 'debug' ? '调试模式' : '运行模式'}`, 'start');
    if (typeof fallbackReason === 'string') {
      emitLog(win, runId, 'warn', fallbackReason, 'start');
    }

    runtimeNodeIds.forEach((nodeId, index) => {
      const delayMs = 320 + index * 420;
      schedule(
        win,
        runId,
        () => {
          const elapsedMs = Date.now() - startedAt;
          const runningStatus = nodeId === 'n9' ? 'skipped' : 'running';
          emit(win, {
            type: 'node:update',
            payload: {
              runId,
              nodeId,
              status: runningStatus,
              badge: runningStatus === 'skipped' ? nodeBadges[nodeId] : nodeBadges[nodeId]
            }
          });

          const [level, message] = nodeLogMessages[nodeId] ?? ['info', `执行节点 ${nodeId}`];
          emitLog(win, runId, level, message, nodeId);


          emit(win, {
            type: 'run:progress',
            payload: {
              runId,
              currentStep: index + 1,
              totalSteps: runtimeNodeIds.length,
              percent: Math.round(((index + 1) / runtimeNodeIds.length) * 100),
              elapsedMs
            }
          });

          schedule(
            win,
            runId,
            () => {
              if (nodeId !== 'n9') {
                emit(win, {
                  type: 'node:update',
                  payload: { runId, nodeId, status: 'done', badge: nodeBadges[nodeId] }
                });
              }
            },
            260
          );
        },
        delayMs
      );
    });

    schedule(
      win,
      runId,
      () => {
        activeRun = null;
        emit(win, {
          type: 'run:finish',
          payload: {
            runId,
            status: 'success',
            finishedAt: new Date().toISOString(),
            message: '流程执行完成'
          }
        });
      },
      320 + runtimeNodeIds.length * 420 + 520
    );

    if (typeof sendEvent === 'function') {
      sendEvent({ runId, status: 'running' });
    }

    return startPayload;
  }

  async function startBackendRun(win, payload = {}) {
    const backendTask =
      typeof payload.flowId === 'string' && payload.flowId.length > 0
        ? await backendClient.runFlow(payload.flowId, {
          mode: payload.mode ?? 'run',
          browserExecutor: payload.browserExecutor,
          variables: payload.variables,
          scope: payload.scope,
          startNodeId: payload.startNodeId,
          failureStrategy: payload.failureStrategy,
          screenshot: payload.screenshot,
          concurrency: payload.concurrency,
          timeoutMs: payload.timeoutMs ?? 30_000
        })
        : await backendClient.startTask({
          flowName: payload.flowName ?? '未命名流程',
          targetUrl: payload.targetUrl ?? '',
          selector: payload.selector ?? '',
          mode: payload.mode ?? 'run',
          browserExecutor: payload.browserExecutor,
          flowDefinition: payload.flowDefinition,
          variables: payload.variables,
          scope: payload.scope,
          startNodeId: payload.startNodeId,
          failureStrategy: payload.failureStrategy,
          screenshot: payload.screenshot,
          concurrency: payload.concurrency,
          adaptive: payload.adaptive !== false,
          autoSave: payload.autoSave !== false,
          timeoutMs: payload.timeoutMs ?? 30_000
        });

    return watchBackendRun(win, backendTask, payload);
  }

  function watchBackendRun(win, backendTask, payload = {}) {
    if (win === null) {
      throw new Error('Window is unavailable.');
    }

    if (activeRun !== null) {
      stopActiveRun('stopped', '上一次运行已被新的运行请求停止');
    }

    const runId = backendTask.taskId;
    const startedAt = Date.now();
    activeRun = {
      backend: true,
      knownNodeIds: collectKnownNodeIds(payload.flowDefinition),
      lastActiveNodeId: null,
      artifactIds: new Set(),
      lastInputPrompt: null,
      lastLogIds: new Set(),
      nodeStates: new Map(),
      runId,
      timers: [],
      usingWebSocket: false,
      win
    };
    const startPayload = {
      runId,
      flowId: backendTask.flowId ?? payload.flowId ?? null,
      status: 'running',
      totalSteps: Math.max(readBackendTotalSteps(backendTask.progress), 1),
      startedAt: new Date(startedAt).toISOString(),
      flowName: payload.flowName ?? '未命名流程',
    };

    emit(win, { type: 'run:start', payload: startPayload });
    emitRunConfigState(win, runId, payload);
    emitVariable(win, runId, { name: 'backend_url', type: 'String', value: backendClient.baseUrl, scope: '全局' });
    if (payload.targetUrl) {
      emitVariable(win, runId, { name: 'target_url', type: 'String', value: payload.targetUrl, scope: '全局' });
    }
    emit(win, { type: 'node:update', payload: { runId, nodeId: 'start', status: 'done' } });

    attachBackendLogSocket(runId);
    schedule(win, runId, () => {
      void pollBackendRun(runId, startedAt);
    }, 250);

    return startPayload;
  }

  async function pollBackendRun(runId, startedAt) {
    if (activeRun === null || activeRun.runId !== runId || activeRun.backend !== true) {
      return;
    }

    try {
      const [snapshot, logs] = await Promise.all([backendClient.getTask(runId), backendClient.getLogs(runId)]);
      const elapsedMs = Date.now() - startedAt;
      const status = normalizeBackendStatus(snapshot.status);
      // When task is complete, always deliver HTTP logs to catch entries the WebSocket may have missed.
      // During active runs, skip HTTP logs when WebSocket is active to avoid ordering issues.
      if (activeRun.usingWebSocket !== true || status !== 'running') {
        for (const log of logs) {
          emitBackendLog(activeRun.win, runId, log);
        }
      }

      // Poll-based fallback: surface the input dialog even when the WebSocket log was missed.
      if (snapshot.inputPrompt != null && snapshot.inputPrompt !== activeRun.lastInputPrompt) {
        activeRun.lastInputPrompt = snapshot.inputPrompt;
        const syntheticId = `${runId}:poll-input`;
        if (!activeRun.lastLogIds.has(syntheticId)) {
          activeRun.lastLogIds.add(syntheticId);
          emit(activeRun.win, {
            type: 'log:append',
            payload: {
              runId,
              id: syntheticId,
              time: formatLogTime(new Date()),
              level: 'input',
              message: snapshot.inputPrompt,
              nodeId: activeRun.lastActiveNodeId ?? 'n1'
            }
          });
        }
      } else if (snapshot.inputPrompt == null) {
        activeRun.lastInputPrompt = null;
      }

      const totalSteps = readBackendTotalSteps(snapshot.progress);
      const currentStep = typeof snapshot.progress?.currentStep === 'number' ? snapshot.progress.currentStep : status === 'running' ? 1 : totalSteps;
      emitBackendVariables(activeRun.win, runId, snapshot.variables);
      emitBackendArtifacts(activeRun.win, runId, snapshot.artifacts);
      emit(activeRun.win, {
        type: 'run:progress',
        payload: {
          runId,
          currentStep,
          totalSteps,
          percent: status === 'running' ? Math.max(snapshot.progress?.percent ?? 10, 10) : 100,
          elapsedMs
        }
      });

      if (status === 'running') {
        schedule(activeRun.win, runId, () => {
          void pollBackendRun(runId, startedAt);
        }, 600);
        return;
      }

      const win = activeRun.win;
      if (activeRun.logSocket !== undefined) {
        activeRun.logSocket.close();
      }
      if (snapshot.status === 'error' && typeof snapshot.progress?.currentStep === 'number') {
        finalizeLastActiveNode(win, runId, 'error');
      } else if (snapshot.status === 'success') {
        finalizeLastActiveNode(win, runId, 'done');
      }
      emit(win, {
        type: 'node:update',
        payload: {
          runId,
          nodeId: 'end',
          status: status === 'success' ? 'done' : status === 'stopped' ? 'skipped' : 'error'
        }
      });
      if (snapshot.result?.count !== undefined) {
        emitVariable(win, runId, { name: 'result_count', type: 'Integer', value: String(snapshot.result.count), scope: '局部' });
      }
      emit(win, {
        type: 'run:finish',
        payload: {
          runId,
          status,
          finishedAt: new Date().toISOString(),
          message: status === 'success' ? '任务执行完成' : snapshot.error ?? '任务执行结束'
        }
      });
      activeRun = null;
    } catch (error) {
      const win = activeRun.win;
      if (activeRun.logSocket !== undefined) {
        activeRun.logSocket.close();
      }
      finalizeLastActiveNode(win, runId, 'error');
      emitLog(win, runId, 'error', `后端任务轮询失败 · ${error.message}`, 'end');
      emit(win, {
        type: 'run:finish',
        payload: {
          runId,
          status: 'error',
          finishedAt: new Date().toISOString(),
          message: '后端任务轮询失败'
        }
      });
      activeRun = null;
    }
  }

  async function stopRun(runId) {
    if (activeRun === null || (typeof runId === 'string' && runId.length > 0 && activeRun.runId !== runId)) {
      return { stopped: false, runId, status: 'ready' };
    }

    if (activeRun.backend === true) {
      const backendRunId = activeRun.runId;
      try {
        await backendClient.stopTask(backendRunId);
      } catch (error) {
        emitLog(activeRun.win, backendRunId, 'warn', `后端停止请求失败 · ${error.message}`, 'end');
      }
      return stopActiveRun('stopped', '流程已停止');
    }

    emitLog(activeRun.win, activeRun.runId, 'warn', '用户请求停止运行 · 清理待执行任务', 'end');
    return stopActiveRun('stopped', '流程已停止');
  }

  async function debugRun(runId, command) {
    if (activeRun === null || (typeof runId === 'string' && runId.length > 0 && activeRun.runId !== runId)) {
      throw new Error('当前没有匹配的运行任务');
    }

    if (activeRun.backend !== true) {
      emitLog(activeRun.win, activeRun.runId, 'running', `调试控制 · ${getDebugCommandLabel(command)}`, 'n1');
      emitVariable(activeRun.win, activeRun.runId, { name: 'debug_command', type: 'String', value: getDebugCommandLabel(command), scope: '局部' });
      return { runId: activeRun.runId, status: 'running' };
    }

    const snapshot = await backendClient.debugTask(activeRun.runId, command);
    return { runId: snapshot.taskId, status: normalizeBackendStatus(snapshot.status) };
  }

  function attachBackendLogSocket(runId) {
    if (typeof WebSocket !== 'function') {
      return;
    }

    try {
      const socket = backendClient.createLogSocket(runId);
      activeRun.logSocket = socket;
      socket.addEventListener('open', () => {
        if (activeRun?.runId === runId) {
          activeRun.usingWebSocket = true;
        }
      });
      socket.addEventListener('message', (event) => {
        const run = activeRun;
        if (run === null || run.runId !== runId) {
          return;
        }
        let parsed;
        try {
          parsed = JSON.parse(event.data);
        } catch {
          return; // JSON 解析失败：静默丢弃，HTTP 轮询会补全日志
        }
        // backend 在 task not found 时发送 {"type":"error",...} — 无 id 字段，不是日志条目
        if (typeof parsed.id !== 'string') {
          return;
        }
        emitBackendLog(run.win, runId, parsed);
      });
      socket.addEventListener('error', () => {
        if (activeRun?.runId === runId) {
          activeRun.usingWebSocket = false;
        }
      });
      socket.addEventListener('close', () => {
        if (activeRun?.runId === runId) {
          activeRun.usingWebSocket = false;
        }
      });
    } catch {
      // WebSocket 只是日志加速通道，失败时由 HTTP 轮询兜底。
    }
  }

  function emitBackendLog(win, runId, log) {
    if (activeRun?.runId === runId && activeRun.lastLogIds.has(log.id)) {
      return;
    }
    if (activeRun?.runId === runId) {
      activeRun.lastLogIds.add(log.id);
    }
    const level = normalizeLogLevel(log.level);
    // Track input prompt so the poll-based fallback can detect duplicates.
    if (level === 'input' && activeRun?.runId === runId) {
      activeRun.lastInputPrompt = log.detail ?? log.message;
      activeRun.lastLogIds.add(`${runId}:poll-input`);
    }
    const nodeId = resolveBackendLogNodeId(log, activeRun?.lastActiveNodeId);
    applyBackendNodeState(win, runId, nodeId, log);
    emit(win, {
      type: 'log:append',
      payload: {
        runId,
        id: log.id,
        time: formatLogTime(new Date(log.time)),
        level,
        message: log.detail ? `${log.message} · ${log.detail}` : log.message,
        nodeId
      }
    });
  }

  return { debugRun, startRun, stopRun, watchBackendRun };
}

function normalizeBackendStatus(status) {
  if (status === 'success') return 'success';
  if (status === 'stopped') return 'stopped';
  if (status === 'error') return 'error';
  return 'running';
}

function normalizeLogLevel(level) {
  if (level === 'success' || level === 'running' || level === 'warn' || level === 'error' || level === 'input') {
    return level;
  }
  return 'info';
}

function resolveBackendLogNodeId(log, lastActiveNodeId) {
  if (typeof log?.nodeId === 'string' && log.nodeId.length > 0) {
    return log.nodeId;
  }
  const message = typeof log?.message === 'string' ? log.message : '';
  if (message.includes('任务启动')) return 'start';
  if (message.includes('任务完成') || message.includes('任务失败') || message.includes('任务已停止')) return 'end';
  return lastActiveNodeId ?? 'start';
}

function emitRunConfigState(win, runId, payload = {}) {
  emitLog(win, runId, 'info', buildRunConfigLogMessage(payload), 'start');
  for (const variable of buildRunConfigVariables(payload)) {
    emitVariable(win, runId, variable);
  }
}

function buildRunConfigLogMessage(payload = {}) {
  const scopeLabel = getRunScopeLabel(payload.scope);
  const failureLabel = getFailureStrategyLabel(payload.failureStrategy);
  const concurrency = normalizeRunConcurrency(payload.concurrency);
  const screenshotLabel = payload.screenshot === false ? '关闭' : '开启';
  const startNodeText = typeof payload.startNodeId === 'string' && payload.startNodeId.length > 0 ? ` · 起点 ${payload.startNodeId}` : '';

  return `运行配置 · 范围 ${scopeLabel} · 并发 ${concurrency} · 失败策略 ${failureLabel} · 截图 ${screenshotLabel}${startNodeText}`;
}

function buildRunConfigVariables(payload = {}) {
  const variables = [
    { name: 'run_scope', type: 'String', value: getRunScopeLabel(payload.scope), scope: '全局' },
    { name: 'run_concurrency', type: 'Integer', value: String(normalizeRunConcurrency(payload.concurrency)), scope: '全局' },
    { name: 'failure_strategy', type: 'String', value: getFailureStrategyLabel(payload.failureStrategy), scope: '全局' },
    { name: 'screenshot_enabled', type: 'Boolean', value: payload.screenshot === false ? 'false' : 'true', scope: '全局' }
  ];

  if (typeof payload.startNodeId === 'string' && payload.startNodeId.length > 0) {
    variables.push({ name: 'start_node_id', type: 'String', value: payload.startNodeId, scope: '全局' });
  }

  return variables;
}

function getRunScopeLabel(scope) {
  return runScopeLabels[scope] ?? runScopeLabels.full;
}

function getFailureStrategyLabel(strategy) {
  return failureStrategyLabels[strategy] ?? failureStrategyLabels.stop;
}

function normalizeRunConcurrency(value) {
  if (!Number.isFinite(value)) {
    return 1;
  }
  return Math.min(20, Math.max(1, Math.round(value)));
}

function getDebugCommandLabel(command) {
  const labels = {
    continue: '继续执行',
    'step-over': '单步越过',
    'step-into': '单步进入'
  };
  return labels[command] ?? '未知调试命令';
}

function emitLog(win, runId, level, message, nodeId) {
  emitRuntimeEvent(win, {
    type: 'log:append',
    payload: {
      runId,
      id: `${runId}-log-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      time: formatLogTime(new Date()),
      level,
      message,
      ...(typeof nodeId === 'string' && nodeId.length > 0 ? { nodeId } : {})
    }
  });
}

function emitVariable(win, runId, variable) {
  emitRuntimeEvent(win, {
    type: 'variable:set',
    payload: { runId, ...variable }
  });
}

function collectKnownNodeIds(flowDefinition) {
  const nodes = flowDefinition?.nodes;
  if (!Array.isArray(nodes)) {
    return new Set(['start', 'end']);
  }
  return new Set(
    nodes
      .map((node) => (node !== null && typeof node === 'object' && typeof node.id === 'string' ? node.id : null))
      .filter((value) => typeof value === 'string')
      .concat(['start', 'end'])
  );
}

function readBackendTotalSteps(progress) {
  if (typeof progress?.totalStep === 'number' && Number.isFinite(progress.totalStep)) {
    return Math.max(1, progress.totalStep);
  }
  if (typeof progress?.totalSteps === 'number' && Number.isFinite(progress.totalSteps)) {
    return Math.max(1, progress.totalSteps);
  }
  return runtimeNodeIds.length;
}

function applyBackendNodeState(win, runId, nodeId, log) {
  if (activeRun === null || activeRun.runId !== runId) {
    return;
  }
  if (typeof nodeId !== 'string' || nodeId.length === 0) {
    return;
  }
  if (activeRun.knownNodeIds instanceof Set && !activeRun.knownNodeIds.has(nodeId)) {
    return;
  }

  const nextStatus = mapBackendLogLevelToNodeStatus(log?.level);
  if (nextStatus === null) {
    return;
  }

  if ((nodeId === 'start' || nodeId === 'end') && nextStatus === 'running') {
    return;
  }

  if (nextStatus === 'running') {
    finalizeLastActiveNode(win, runId, 'done', nodeId);
    activeRun.lastActiveNodeId = nodeId;
  } else if (activeRun.lastActiveNodeId === nodeId) {
    activeRun.lastActiveNodeId = null;
  }

  const currentStatus = activeRun.nodeStates.get(nodeId);
  if (currentStatus === nextStatus || isTerminalNodeStatus(currentStatus)) {
    return;
  }
  activeRun.nodeStates.set(nodeId, nextStatus);
  emitRuntimeEvent(win, {
    type: 'node:update',
    payload: {
      runId,
      nodeId,
      status: nextStatus
    }
  });
}

function finalizeLastActiveNode(win, runId, status, exceptNodeId) {
  if (activeRun === null || activeRun.runId !== runId) {
    return;
  }
  const nodeId = activeRun.lastActiveNodeId;
  if (typeof nodeId !== 'string' || nodeId.length === 0 || nodeId === exceptNodeId) {
    return;
  }
  activeRun.lastActiveNodeId = null;
  activeRun.nodeStates.set(nodeId, status);
  emitRuntimeEvent(win, {
    type: 'node:update',
    payload: {
      runId,
      nodeId,
      status
    }
  });
}

function mapBackendLogLevelToNodeStatus(level) {
  if (level === 'running' || level === 'info') {
    return 'running';
  }
  if (level === 'success') {
    return 'done';
  }
  if (level === 'error') {
    return 'error';
  }
  return null;
}

function isTerminalNodeStatus(status) {
  return status === 'done' || status === 'error' || status === 'skipped';
}

function emitBackendVariables(win, runId, variables) {
  if (!Array.isArray(variables)) {
    return;
  }
  for (const variable of variables) {
    if (typeof variable?.name === 'string') {
      emitVariable(win, runId, variable);
    }
  }
}

function emitBackendArtifacts(win, runId, artifacts) {
  if (!Array.isArray(artifacts) || activeRun?.runId !== runId) {
    return;
  }
  const nextIds = new Set(artifacts.map((artifact) => artifact.artifactId));
  const hasChanged = nextIds.size !== activeRun.artifactIds.size || [...nextIds].some((artifactId) => !activeRun.artifactIds.has(artifactId));
  if (!hasChanged) {
    return;
  }
  activeRun.artifactIds = nextIds;
  emitRuntimeEvent(win, {
    type: 'artifacts:update',
    payload: { runId, artifacts }
  });
}

function emitRuntimeEvent(win, event) {
  if (win !== null && !win.isDestroyed()) {
    win.webContents.send(IPC_CHANNELS.run.event, event);
  }
}

function formatLogTime(date) {
  return [
    String(date.getHours()).padStart(2, '0'),
    String(date.getMinutes()).padStart(2, '0'),
    String(date.getSeconds()).padStart(2, '0')
  ].join(':') + `.${String(date.getMilliseconds()).padStart(3, '0')}`;
}

async function generateScraplingScript(payload = {}, backendClient = new BackendClient()) {
  if (payload.backend !== 'mock') {
    try {
      return await backendClient.generateScript(payload);
    } catch {
      // 后端不可用时继续使用本地模板，保证桌面端离线仍可生成可编辑脚本。
    }
  }

  const flowName = sanitizeComment(payload.flowName ?? '未命名流程');
  const flowDefinition = JSON.stringify(payload.flowDefinition && typeof payload.flowDefinition === 'object' ? payload.flowDefinition : {}, null, 2);

  return {
    filename: `${slugify(flowName)}.py`,
    language: 'python',
    dependencies: ['scrapling[all]>=0.3.0'],
    content: [
      'from __future__ import annotations',
      '',
      'import json',
      'from scrapling.fetchers import Fetcher',
      '',
      `FLOW_DEFINITION = json.loads(${JSON.stringify(flowDefinition)})`,
      '',
      '',
      'def run() -> dict:',
      `    """运行从 Easy RPA 生成的 ${flowName} 全流程 Scrapling 脚本。"""`,
      '    # 后端不可用时生成的离线模板：保留完整流程定义，便于手动补全。',
      '    # 重新连接后端可生成包含节点映射逻辑的完整脚本。',
      '    return {"flow": FLOW_DEFINITION}',
      '',
      '',
      'if __name__ == "__main__":',
      '    print(json.dumps(run(), ensure_ascii=False, indent=2))',
      ''
    ].join('\n')
  };
}

function sanitizePythonString(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\r?\n/g, ' ');
}

function sanitizeComment(value) {
  return String(value).replace(/\r?\n/g, ' ').trim() || '未命名流程';
}

function slugify(value) {
  const ascii = String(value)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');

  return ascii.length > 0 ? ascii : 'rpa-flow';
}

module.exports = {
  createRuntimeController,
  generateScraplingScript,
  runtimeNodeIds
};
