import { useEffect, useState } from 'react';
import { POLL_INTERVAL_MS, type ConnectionState, type ConnectionStatus } from '../lib/connection';

export function useConnectionStatus(): ConnectionState {
  const [state, setState] = useState<ConnectionState>({ phase: 'checking', status: null });

  useEffect(() => {
    let cancelled = false;

    const poll = async (): Promise<void> => {
      try {
        const response = (await browser.runtime.sendMessage({ type: 'getConnectionStatus' })) as ConnectionStatus;
        if (!cancelled) {
          setState({ phase: 'ready', status: response });
        }
      } catch {
        if (!cancelled) {
          setState({ phase: 'error', status: null });
        }
      }
    };

    void poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return state;
}
