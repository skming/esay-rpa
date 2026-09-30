export interface ManagedTab {
  title: string;
  url: string;
}

export interface ManagedGroup {
  title: string;
  color: string;
}

export interface ConnectionStatus {
  connected: boolean;
  backendBaseUrl: string;
  bridgeUrl: string;
  heartbeatSeconds: number;
  // 以下托管信息仅在 connected 时有值：桥断开时「在托管什么」无意义，后台一律回传空。
  controlledTab: ManagedTab | null;
  ownedTabCount: number;
  group: ManagedGroup | null;
  explorationTab: ManagedTab | null;
}

export type ConnectionPhase = 'checking' | 'ready' | 'error';

export interface ConnectionState {
  phase: ConnectionPhase;
  status: ConnectionStatus | null;
}

export const POLL_INTERVAL_MS = 2000;
