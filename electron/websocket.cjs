// 后端 REST 地址是唯一配置入口；WebSocket 地址从这里派生，避免多处手写
// http -> ws 转换导致 https/wss 或末尾斜杠处理不一致。
function toWebSocketBaseUrl(baseUrl) {
  const normalized = String(baseUrl || '').replace(/\/$/, '');
  if (normalized.startsWith('https://')) {
    return `wss://${normalized.slice('https://'.length)}`;
  }
  if (normalized.startsWith('http://')) {
    return `ws://${normalized.slice('http://'.length)}`;
  }
  if (normalized.startsWith('ws://') || normalized.startsWith('wss://')) {
    return normalized;
  }
  throw new Error('Backend URL must start with http://, https://, ws:// or wss://');
}

function buildWebSocketUrl(baseUrl, path) {
  const normalizedPath = String(path || '').startsWith('/') ? path : `/${path}`;
  return `${toWebSocketBaseUrl(baseUrl)}${normalizedPath}`;
}

module.exports = {
  buildWebSocketUrl,
  toWebSocketBaseUrl
};
