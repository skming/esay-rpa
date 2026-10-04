const {
  normalizeRuntimeStatus, normalizeLogLevel, resolveBackendLogNodeId, collectKnownNodeIds,
  readBackendTotalSteps, formatLogTime, applyBackendNodeState, finalizeLastActiveNode
} = require('../shared/runtimeEvents.cjs');
const { normalizeConcurrency } = require('../shared/backendPayloads.cjs');
const { BackendClient } = require('./backendClient.cjs');
const { IPC_CHANNELS } = require('./ipcChannels.cjs');

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

function createRuntimeController({ backendClient = new BackendClient() } = {}) {
  function emit(win, event) {
    if (win !== null && !win.isDestroyed()) {
      win.webContents.send(IPC_CHANNELS.run.event, event);
    }
  }

  function stopActiveRun(status, message) {
    if (activeRun === null) {
      return { stopped: false, status: 'ready' };
    }

    const { runId, win } = activeRun;
    finalizeLastActiveNode(activeRun, event => emit(win, event), 'skipped');
    clearActiveRun();
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

  function clearActiveRun() {
    if (activeRun === null) {
      return;
    }
    const run = activeRun;
    activeRun = null;
    run.timers.forEach(timer => clearTimeout(timer));
    run.logSocket?.close();
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
      await stopRun(activeRun.runId);
    }
    return startBackendRun(win, payload);
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

    clearActiveRun();

    const runId = backendTask.taskId;
    const startedAt = Date.now();
    activeRun = {
      knownNodeIds: collectKnownNodeIds(payload.flowDefinition),
      lastActiveNodeId: null,
      artifactIds: new Set(),
      lastConfirmationMessage: null,
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
    if (activeRun === null || activeRun.runId !== runId) {
      return;
    }

    const run = activeRun;
    try {
      const [snapshot, logs] = await Promise.all([backendClient.getTask(runId), backendClient.getLogs(runId)]);
      if (activeRun !== run) {
        return;
      }
      const elapsedMs = Date.now() - startedAt;
      const status = normalizeRuntimeStatus(snapshot.status);
      // When task is complete, always deliver HTTP logs to catch entries the WebSocket may have missed.
      // During active runs, skip HTTP logs when WebSocket is active to avoid ordering issues.
      if (activeRun.usingWebSocket !== true || status !== 'running') {
        for (const log of logs) {
          emitBackendLog(activeRun.win, runId, log);
        }
      }

      // Poll-based fallback: surface the sensitive-action confirmation even when the WebSocket log was missed.
      if (snapshot.confirmationMessage != null && snapshot.confirmationMessage !== activeRun.lastConfirmationMessage) {
        activeRun.lastConfirmationMessage = snapshot.confirmationMessage;
        const syntheticId = `${runId}:poll-confirmation`;
        if (!activeRun.lastLogIds.has(syntheticId)) {
          activeRun.lastLogIds.add(syntheticId);
          emit(activeRun.win, {
            type: 'log:append',
            payload: {
              runId,
              id: syntheticId,
              time: formatLogTime(new Date()),
              level: 'input',
              message: snapshot.confirmationMessage,
              nodeId: activeRun.lastActiveNodeId ?? 'start'
            }
          });
        }
      } else if (snapshot.confirmationMessage == null && activeRun.lastConfirmationMessage !== null) {
        // 确认被解决/超时清空后派发清除，否则桌面确认浮层会一直挂到 run:finish。
        activeRun.lastConfirmationMessage = null;
        emit(activeRun.win, { type: 'run:confirmation', payload: { runId, message: null } });
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
      finalizeLastActiveNode(activeRun, event => emit(win, event), status === 'error' ? 'error' : status === 'stopped' ? 'skipped' : 'done');
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
      if (activeRun !== run) {
        return;
      }
      const win = run.win;
      if (activeRun.logSocket !== undefined) {
        activeRun.logSocket.close();
      }
      finalizeLastActiveNode(activeRun, event => emit(win, event), 'error');
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

    const run = activeRun;
    await backendClient.stopTask(run.runId);
    if (activeRun !== run) {
      return { stopped: true, runId: run.runId, status: 'stopped' };
    }
    return stopActiveRun('stopped', '流程已停止');
  }

  async function debugRun(runId, command) {
    if (activeRun === null || (typeof runId === 'string' && runId.length > 0 && activeRun.runId !== runId)) {
      throw new Error('当前没有匹配的运行任务');
    }

    const snapshot = await backendClient.debugTask(activeRun.runId, command);
    return { runId: snapshot.taskId, status: normalizeRuntimeStatus(snapshot.status) };
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
    // Track the confirmation message so the poll-based fallback can detect duplicates.
    if (level === 'input' && activeRun?.runId === runId) {
      activeRun.lastConfirmationMessage = log.detail ?? log.message;
      activeRun.lastLogIds.add(`${runId}:poll-confirmation`);
    }
    const nodeId = resolveBackendLogNodeId(log, activeRun?.lastActiveNodeId);
    if (activeRun?.runId === runId) {
      applyBackendNodeState(activeRun, event => emit(win, event), nodeId, log);
    }
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

function emitRunConfigState(win, runId, payload = {}) {
  emitLog(win, runId, 'info', buildRunConfigLogMessage(payload), 'start');
  for (const variable of buildRunConfigVariables(payload)) {
    emitVariable(win, runId, variable);
  }
}

function buildRunConfigLogMessage(payload = {}) {
  const scopeLabel = getRunScopeLabel(payload.scope);
  const failureLabel = getFailureStrategyLabel(payload.failureStrategy);
  const concurrency = normalizeConcurrency(payload.concurrency);
  const screenshotLabel = payload.screenshot === false ? '关闭' : '开启';
  const startNodeText = typeof payload.startNodeId === 'string' && payload.startNodeId.length > 0 ? ` · 起点 ${payload.startNodeId}` : '';

  return `运行配置 · 范围 ${scopeLabel} · 并发 ${concurrency} · 失败策略 ${failureLabel} · 截图 ${screenshotLabel}${startNodeText}`;
}

function buildRunConfigVariables(payload = {}) {
  const variables = [
    { name: 'run_scope', type: 'String', value: getRunScopeLabel(payload.scope), scope: '全局' },
    { name: 'run_concurrency', type: 'Integer', value: String(normalizeConcurrency(payload.concurrency)), scope: '全局' },
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

async function generateScraplingScript(payload = {}, backendClient = new BackendClient()) {
  let degradedReason;
  try {
    return await backendClient.generateScript(payload);
  } catch (error) {
    // 离线模板不能执行抓取，失败原因必须回到调用方以显示降级提示。
    degradedReason = (error instanceof Error ? error.message : String(error)) || '后端脚本生成失败';
  }

  const flowName = sanitizeComment(payload.flowName ?? '未命名流程');
  const flowDefinition = JSON.stringify(payload.flowDefinition && typeof payload.flowDefinition === 'object' ? payload.flowDefinition : {}, null, 2);
  const degradedHeader = [
    '# 后端不可用，这是离线模板：只保留流程定义，不含任何抓取逻辑。',
    `# 失败原因：${sanitizeComment(degradedReason)}`,
    '# 连上 Easy RPA 后端重新生成，才能得到可运行的脚本。',
    ''
  ];

  return {
    filename: `${slugify(flowName)}.py`,
    language: 'python',
    dependencies: ['scrapling[fetchers]>=0.4.10'],
    degraded: true,
    degradedReason,
    content: [
      ...degradedHeader,
      'from __future__ import annotations',
      '',
      'import json',
      'from scrapling.fetchers import Fetcher',
      '',
      `FLOW_DEFINITION = json.loads(${JSON.stringify(flowDefinition)})`,
      '',
      '',
      'def run() -> dict:',
      `    """离线模板：${flowName} 的流程定义，抓取逻辑需手动补全。"""`,
      '    # 直接抛错而不是返回流程定义：打印一份 JSON 再退出 0，等于把「一条数据都没抓到」报成成功。',
      '    raise RuntimeError("离线模板没有抓取逻辑：请连上 Easy RPA 后端重新生成脚本")',
      '',
      '',
      'if __name__ == "__main__":',
      '    print(json.dumps(run(), ensure_ascii=False, indent=2))',
      ''
    ].join('\n')
  };
}

function sanitizeComment(value) {
  return String(value).replace(/\r?\n/g, ' ').trim() || '未命名流程';
}

function slugify(value) {
  // 与后端 code_generator._slugify 一致按 Unicode 词字符切：只留 [a-z0-9] 会把中文流程名
  // 整段吃掉，两个中文流程导出后都叫 rpa-flow.py，第二个直接覆盖第一个。
  const slug = String(value)
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}_]+/gu, '-')
    .replace(/^-+|-+$/g, '');

  return slug.length > 0 ? slug : 'rpa-flow';
}

module.exports = {
  createRuntimeController,
  generateScraplingScript
};
