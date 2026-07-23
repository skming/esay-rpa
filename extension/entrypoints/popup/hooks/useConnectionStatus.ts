import { useEffect, useState } from 'react';
import { POLL_INTERVAL_MS, type ConnectionStatus } from '../lib/connection';

export function useConnectionStatus(): ConnectionStatus | null {
  const [status, setStatus] = useState<ConnectionStatus | null>(null);

  useEffect(() => {
    let cancelled = false;

    const poll = async (): Promise<void> => {
      try {
        const response = (await browser.runtime.sendMessage({ type: 'getConnectionStatus' })) as ConnectionStatus;
        if (!cancelled) {
          setStatus(response);
        }
      } catch {
        if (!cancelled) {
          setStatus(null);
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

  return status;
}
