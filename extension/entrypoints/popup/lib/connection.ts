export interface ConnectionStatus {
  connected: boolean;
  backendBaseUrl: string;
}

export type ConnectionPhase = 'checking' | 'ready' | 'error';

export interface ConnectionState {
  phase: ConnectionPhase;
  status: ConnectionStatus | null;
}

export const POLL_INTERVAL_MS = 2000;
