const { ipcMain } = require('electron');
const { DEFAULT_BACKEND_URL } = require('./backendClient.cjs');
const { IPC_CHANNELS } = require('./ipcChannels.cjs');
const { buildWebSocketUrl } = require('./websocket.cjs');

function createPickerService({ onResult, onCancel }) {
  let resultSocket = null;
  let _settled = false;  // prevent double-firing cancel on close after capture

  function _fireCancel() {
    if (_settled) return;
    _settled = true;
    onCancel?.();
  }

  function closePicker() {
    if (resultSocket !== null) {
      try { resultSocket.close(1000); } catch {}
      resultSocket = null;
    }
    // Best-effort close on the backend side
    fetch(`${DEFAULT_BACKEND_URL}/api/browser/picker/close`, { method: 'POST' }).catch(() => {});
    return { status: 'closed' };
  }

  async function openPicker(_parentWindow, payload = {}) {
    const targetUrl = normalizeTargetUrl(payload.targetUrl);
    const mode = payload.mode === 'browse' ? 'browse' : 'pick';

    // Close any previous session first
    _settled = false;
    closePicker();

    // Ask backend to open the headed Playwright browser (shares full session state)
    const openRes = await fetch(`${DEFAULT_BACKEND_URL}/api/browser/picker/open`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ targetUrl, mode })
    });
    if (!openRes.ok) {
      const body = await openRes.json().catch(() => ({}));
      throw new Error(body.detail ?? `浏览器启动失败 (${openRes.status})`);
    }

    // Browse mode: just open the browser, no picker UI or WS needed
    if (mode === 'browse') {
      return { status: 'ready', mode: 'browse' };
    }

    // 拾取器结果也从统一工具派生 WebSocket 地址，确保 http/https 与末尾斜杠处理一致。
    const wsUrl = buildWebSocketUrl(DEFAULT_BACKEND_URL, '/ws/picker');
    const ws = new WebSocket(wsUrl);
    resultSocket = ws;

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(String(event.data));
        if (msg.type === 'capture') {
          _settled = true;
          onResult({
            selector: String(msg.selector || ''),
            strategy: 'css',
            confidence: Number.isFinite(msg.confidence) ? msg.confidence : 0.78,
            text: String(msg.text || ''),
            url: String(msg.url || targetUrl),
            capturedAt: new Date().toISOString()
          });
        } else if (msg.type === 'cancel') {
          _fireCancel();
        }
      } catch {}
      resultSocket = null;
    };

    ws.onclose = () => { resultSocket = null; _fireCancel(); };
    ws.onerror = () => { resultSocket = null; _fireCancel(); };

    return { status: 'ready', mode: 'selector-picker' };
  }

  ipcMain.on(IPC_CHANNELS.picker.cancel, () => {
    closePicker();
  });

  return { closePicker, openPicker };
}

function normalizeTargetUrl(value) {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error('请先配置目标页面地址后再启动拾取器');
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

module.exports = {
  createPickerService
};
