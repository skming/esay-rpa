/** 从后端 HTTP(S) 地址派生 WebSocket 地址，使浏览器模式与 Electron 模式共用同一套后端地址配置。 */
export function toWebSocketBaseUrl(baseUrl: string): string {
  const normalized = String(baseUrl).replace(/\/$/, '');
  if (normalized.startsWith('https://')) {
    return `wss://${normalized.slice('https://'.length)}`;
  }
  if (normalized.startsWith('http://')) {
    return `ws://${normalized.slice('http://'.length)}`;
  }
  if (normalized.startsWith('ws://') || normalized.startsWith('wss://')) {
    return normalized;
  }
  throw new Error('Backend URL 必须以 http://、https://、ws:// 或 wss:// 开头');
}

export function buildWebSocketUrl(baseUrl: string, path: string): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${toWebSocketBaseUrl(baseUrl)}${normalizedPath}`;
}
