export interface ConnectionStatus {
  connected: boolean;
  backendBaseUrl: string;
}

export const POLL_INTERVAL_MS = 2000;

export function getBackendBaseUrlLabel(status: ConnectionStatus | null): string {
  return status?.backendBaseUrl ?? '等待后端握手';
}
