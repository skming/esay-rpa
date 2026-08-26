export interface ConnectionStatus {
  connected: boolean;
  backendBaseUrl: string;
}

export const POLL_INTERVAL_MS = 2000;

/** status 为 null 只有一种原因：background 没回消息（SW 正在冷启动或已崩）。
 *  后端地址是编译期常量、不来自任何握手，所以这里不能写"等待后端握手"——那是在替一个
 *  从没发生过的过程编故事，用户会照着去查后端而不是查插件。 */
export function getBackendBaseUrlLabel(status: ConnectionStatus | null): string {
  return status?.backendBaseUrl ?? '插件未响应';
}
