import { useEffect } from 'react';

import type { ContextMenuAction } from '../types/rpa';

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tagName = target.tagName.toLowerCase();
  return tagName === 'input' || tagName === 'textarea' || tagName === 'select' || target.isContentEditable;
}

export function useStudioShortcuts({
  onContextAction,
  onDeleteEdge,
  onFocusProperties,
  onSave,
  onSelectNode,
  onUndo,
  selectedEdgeId,
  selectedNodeId
}: {
  onContextAction: (action: ContextMenuAction, nodeId: string) => void;
  onDeleteEdge: (edgeId: string) => void;
  onFocusProperties?: () => void;
  onSave: () => void;
  onSelectNode: (nodeId: string) => void;
  onUndo: () => void;
  selectedEdgeId: string | null;
  selectedNodeId: string;
}): void {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (event.target instanceof HTMLElement) {
          event.target.blur();
        }
        onSelectNode('start');
        return;
      }

      if (isEditableTarget(event.target)) {
        return;
      }

      const key = event.key.toLowerCase();
      const modifier = event.metaKey || event.ctrlKey;

      if (event.key === 'Enter' && !modifier) {
        event.preventDefault();
        onFocusProperties?.();
        return;
      }

      if (modifier && key === 's') {
        event.preventDefault();
        onSave();
        return;
      }

      if (modifier && key === 'z') {
        event.preventDefault();
        onUndo();
        return;
      }

      if (modifier && key === 'd') {
        event.preventDefault();
        onContextAction('duplicate', selectedNodeId);
        return;
      }

      if (key === 'b') {
        event.preventDefault();
        onContextAction('breakpoint', selectedNodeId);
        return;
      }

      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault();
        if (selectedEdgeId !== null) {
          onDeleteEdge(selectedEdgeId);
        } else {
          onContextAction('delete', selectedNodeId);
        }
        return;
      }

    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onContextAction, onDeleteEdge, onSave, onSelectNode, onUndo, selectedEdgeId, selectedNodeId]);
}
