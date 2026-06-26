import { useCallback, useRef } from 'react';
import type { Edge, Node } from '@xyflow/react';
import type { RpaNodeData } from '../types/rpa';

type Snapshot = { nodes: Node<RpaNodeData>[]; edges: Edge[] };

const MAX_HISTORY = 50;

export function useUndoHistory(): {
  pushHistory: (nodes: Node<RpaNodeData>[], edges: Edge[]) => void;
  undo: (
    setNodes: (nodes: Node<RpaNodeData>[]) => void,
    setEdges: (edges: Edge[]) => void
  ) => boolean;
} {
  const stackRef = useRef<Snapshot[]>([]);

  const pushHistory = useCallback((nodes: Node<RpaNodeData>[], edges: Edge[]): void => {
    const snapshot: Snapshot = {
      nodes: nodes.map((n) => ({ ...n, position: { ...n.position }, data: { ...n.data } })),
      edges: edges.map((e) => ({ ...e }))
    };
    stackRef.current.push(snapshot);
    if (stackRef.current.length > MAX_HISTORY) {
      stackRef.current.shift();
    }
  }, []);

  const undo = useCallback(
    (setNodes: (nodes: Node<RpaNodeData>[]) => void, setEdges: (edges: Edge[]) => void): boolean => {
      const snapshot = stackRef.current.pop();
      if (snapshot === undefined) return false;
      setNodes(snapshot.nodes);
      setEdges(snapshot.edges);
      return true;
    },
    []
  );

  return { pushHistory, undo };
}
