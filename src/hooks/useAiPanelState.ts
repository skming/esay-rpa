import { useState } from 'react';
import { useAiPanelStore } from '../stores/useAiPanelStore';

export function useAiPanelState() {
  const { open: aiPanelOpen, setOpen: setAiPanelOpen, mode, setMode, busy: aiBusy, close } = useAiPanelStore();
  const [aiPendingMessage, setAiPendingMessage] = useState<string | null>(null);

  return {
    aiPanelOpen, setAiPanelOpen, aiBusy,
    aiPanelMode: mode, setAiPanelMode: setMode,
    aiPendingMessage, setAiPendingMessage,
    closePanel: close,
  };
}
