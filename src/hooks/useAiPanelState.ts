import { useState } from 'react';
import { useAiPanelStore } from '../stores/useAiPanelStore';

export function useAiPanelState() {
  const { open: aiPanelOpen, setOpen: setAiPanelOpen, mode, setMode, busy: aiBusy, close } = useAiPanelStore();
  const [aiPendingMessage, setAiPendingMessage] = useState<string | null>(null);

  const setAiPanelMode = (next: 'sidebar' | 'float') => setMode(next);

  const closePanel = () => {
    close();
  };

  return {
    aiPanelOpen, setAiPanelOpen, aiBusy,
    aiPanelMode: mode, setAiPanelMode,
    aiPendingMessage, setAiPendingMessage,
    closePanel,
  };
}
