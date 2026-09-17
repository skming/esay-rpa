const { DEFAULT_BACKEND_URL } = require('./backendClient.cjs');
const { buildWebSocketUrl } = require('./websocket.cjs');

function createPickerService({ onResult, onCancel, onError, fetchImpl = fetch, WebSocketCtor = WebSocket }) {
  let activeSession = null;
  let operations = Promise.resolve();
  function serialize(action) {
    const next = operations.then(action);
    operations = next.catch(() => {});
    return next;
  }

  function settle(session, terminal) {
    if (session.settled) return;
    session.settled = true;
    if (activeSession === session) {
      activeSession = null;
    }
    if (terminal.type === 'capture') {
      onResult?.(terminal);
    } else if (terminal.type === 'cancel') {
      onCancel?.(terminal);
    } else {
      onError?.(terminal);
    }
    try { session.socket?.close(1000); } catch {}
  }

  function settleError(session, message) {
    settle(session, { ...session.request, message, type: 'error' });
  }

  function settleCancel(session, reason = 'cancelled') {
    settle(session, { ...session.request, reason, type: 'cancel' });
  }

  async function closePicker(payload = {}) {
    const requestId = readRequiredString(payload.requestId, 'requestId');
    const session = activeSession;
    if (session === null || session.request.requestId !== requestId) {
      return { requestId, status: 'closed' };
    }
    if (session.closePromise !== null) {
      return session.closePromise;
    }

    session.closePromise = (async () => {
      const response = await fetchImpl(`${DEFAULT_BACKEND_URL}/api/browser/picker/close`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ requestId })
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? `元素拾取器关闭失败 (${response.status})`);
      }
      settleCancel(session, 'closed');
      return { requestId, status: 'closed' };
    })();
    try {
      return await session.closePromise;
    } catch (error) {
      session.closePromise = null;
      throw error;
    }
  }

  async function openPicker(_parentWindow, payload = {}) {
    const request = normalizePickerRequest(payload);

    if (activeSession !== null) {
      await closePicker({ requestId: activeSession.request.requestId });
    }

    const openRes = await fetchImpl(`${DEFAULT_BACKEND_URL}/api/browser/picker/open`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(request)
    });
    if (!openRes.ok) {
      const body = await openRes.json().catch(() => ({}));
      throw new Error(body.detail ?? `浏览器启动失败 (${openRes.status})`);
    }

    if (request.mode === 'browse') {
      return { requestId: request.requestId, status: 'ready', mode: 'browse' };
    }

    const session = createSession(request);
    activeSession = session;
    const wsUrl = new URL(buildWebSocketUrl(DEFAULT_BACKEND_URL, '/ws/picker'));
    wsUrl.searchParams.set('requestId', request.requestId);

    try {
      const ws = new WebSocketCtor(wsUrl);
      session.socket = ws;
      ws.onmessage = (event) => {
        if (activeSession !== session || session.settled) return;
        try {
          const message = JSON.parse(String(event.data));
          if (message.requestId !== request.requestId) {
            settleError(session, '拾取器返回了不匹配的请求标识');
            return;
          }
          if (message.type === 'capture') {
            const selector = typeof message.selector === 'string' ? message.selector : '';
            if (!selector || !Number.isInteger(message.matches) || message.matches < 1 || message.selectedIncluded !== true
                || (request.selectionMode === 'single' && message.matches !== 1)) {
              settleError(session, '拾取结果未通过定位校验');
              return;
            }
            settle(session, {
              ...request,
              capturedAt: typeof message.capturedAt === 'string' ? message.capturedAt : new Date().toISOString(),
              documentId: typeof message.documentId === 'string' ? message.documentId : undefined,
              matches: Number.isFinite(message.matches) ? message.matches : undefined,
              selectedIncluded: typeof message.selectedIncluded === 'boolean' ? message.selectedIncluded : undefined,
              selector,
              strategy: message.strategy === 'xpath' || message.strategy === 'text' ? message.strategy : 'css',
              tabId: Number.isInteger(message.tabId) ? message.tabId : undefined,
              text: typeof message.text === 'string' ? message.text : '',
              type: 'capture',
              url: typeof message.url === 'string' ? message.url : request.targetUrl ?? '',
              usesPosition: typeof message.usesPosition === 'boolean' ? message.usesPosition : undefined
            });
            return;
          }
          if (message.type === 'cancel') {
            settleCancel(session, normalizeCancelReason(message.reason));
            return;
          }
          if (message.type === 'error') {
            settleError(session, typeof message.message === 'string' && message.message.trim() ? message.message : '元素拾取器发生错误');
            return;
          }
          settleError(session, '拾取器返回了未知结果类型');
        } catch {
          settleError(session, '拾取器返回了无效结果');
        }
      };
      ws.onclose = () => {
        if (activeSession === session && !session.settled) {
          settleCancel(session, 'closed');
        }
      };
      ws.onerror = () => {
        if (activeSession === session && !session.settled) {
          settleError(session, '元素拾取器连接失败');
        }
      };
    } catch {
      session.settled = true;
      if (activeSession === session) activeSession = null;
      await fetchImpl(`${DEFAULT_BACKEND_URL}/api/browser/picker/close`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ requestId: request.requestId })
      }).catch(() => {});
      throw new Error('元素拾取器连接失败');
    }

    return { requestId: request.requestId, status: 'ready', mode: 'selector-picker' };
  }

  return {
    closePicker: (payload) => serialize(() => closePicker(payload)),
    openPicker: (parent, payload) => serialize(() => openPicker(parent, payload))
  };
}

function createSession(request) {
  return {
    closePromise: null,
    request,
    settled: false,
    socket: null
  };
}

function normalizePickerRequest(payload) {
  const mode = payload.mode === 'browse' ? 'browse' : 'pick';
  const browserExecutor = payload.browserExecutor === 'extension' ? 'extension' : 'playwright';
  const request = {
    browserExecutor,
    mode,
    requestId: readRequiredString(payload.requestId, 'requestId')
  };
  const targetUrl = normalizeTargetUrl(payload.targetUrl, { required: browserExecutor === 'playwright' });
  if (targetUrl !== undefined) {
    request.targetUrl = targetUrl;
  }
  if (mode === 'pick') {
    request.flowId = readRequiredString(payload.flowId, 'flowId');
    request.nodeId = readRequiredString(payload.nodeId, 'nodeId');
    request.field = payload.field === 'targetSelector' ? 'targetSelector' : payload.field === 'selector' ? 'selector' : invalidField();
    request.selectionMode = payload.selectionMode === 'multiple' ? 'multiple' : 'single';
  }
  return request;
}

function invalidField() {
  throw new Error('field 必须是 selector 或 targetSelector');
}

function readRequiredString(value, name) {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`${name} 不能为空`);
  }
  return value.trim();
}

function normalizeTargetUrl(value, { required }) {
  if (typeof value !== 'string' || !value.trim()) {
    if (required) {
      throw new Error('请先配置目标页面地址后再启动拾取器');
    }
    return undefined;
  }
  const trimmed = value.trim();
  if (trimmed.includes('${')) {
    throw new Error('目标页面地址包含未解析的变量，请直接输入实际 URL');
  }
  if (!/^https?:\/\//i.test(trimmed)) {
    throw new Error('目标页面地址必须以 http:// 或 https:// 开头');
  }
  return trimmed;
}

function normalizeCancelReason(value) {
  return value === 'replaced' || value === 'closed' ? value : 'cancelled';
}

module.exports = {
  createPickerService
};
