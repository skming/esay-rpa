function normalizeRuntimeStatus(status) {
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

function collectKnownNodeIds(flowDefinition) {
  const nodes = flowDefinition?.nodes;
  if (!Array.isArray(nodes)) {
    return null;
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
  return 1;
}

function formatLogTime(date) {
  return [
    String(date.getHours()).padStart(2, '0'),
    String(date.getMinutes()).padStart(2, '0'),
    String(date.getSeconds()).padStart(2, '0')
  ].join(':') + `.${String(date.getMilliseconds()).padStart(3, '0')}`;
}

function applyBackendNodeState(run, emit, nodeId, log) {
  if (typeof nodeId !== 'string' || nodeId.length === 0) {
    return;
  }
  if (run.knownNodeIds instanceof Set && !run.knownNodeIds.has(nodeId)) {
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
    finalizeLastActiveNode(run, emit, 'done', nodeId);
    run.lastActiveNodeId = nodeId;
  } else if (run.lastActiveNodeId === nodeId) {
    run.lastActiveNodeId = null;
  }

  const currentStatus = run.nodeStates.get(nodeId);
  if (currentStatus === nextStatus || (isTerminalNodeStatus(currentStatus) && nextStatus !== 'running')) {
    return;
  }
  run.nodeStates.set(nodeId, nextStatus);
  emit({
    type: 'node:update',
    payload: {
      runId: run.runId,
      nodeId,
      status: nextStatus
    }
  });
}

function finalizeLastActiveNode(run, emit, status, exceptNodeId) {
  const nodeId = run.lastActiveNodeId;
  if (typeof nodeId !== 'string' || nodeId.length === 0 || nodeId === exceptNodeId) {
    return;
  }
  run.lastActiveNodeId = null;
  run.nodeStates.set(nodeId, status);
  emit({
    type: 'node:update',
    payload: {
      runId: run.runId,
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

module.exports = {
  normalizeRuntimeStatus, normalizeLogLevel, resolveBackendLogNodeId, collectKnownNodeIds,
  readBackendTotalSteps, formatLogTime, applyBackendNodeState, finalizeLastActiveNode
};
