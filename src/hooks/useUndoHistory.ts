import { useCallback, useRef } from 'react';
import type { Edge, Node } from '@xyflow/react';
import type { RpaNodeData } from '../types/rpa';

type Snapshot = { nodes: Node<RpaNodeData>[]; edges: Edge[] };

type SetNodes = (nodes: Node<RpaNodeData>[]) => void;
type SetEdges = (edges: Edge[]) => void;

const MAX_HISTORY = 50;

// 深拷贝节点/边，避免后续对画布状态的原地修改污染已入栈的历史快照
function cloneSnapshot(nodes: Node<RpaNodeData>[], edges: Edge[]): Snapshot {
  return {
    nodes: nodes.map((n) => ({ ...n, position: { ...n.position }, data: { ...n.data } })),
    edges: edges.map((e) => ({ ...e }))
  };
}

export function useUndoHistory(): {
  pushHistory: (nodes: Node<RpaNodeData>[], edges: Edge[]) => void;
  undo: (current: Snapshot, setNodes: SetNodes, setEdges: SetEdges) => boolean;
  redo: (current: Snapshot, setNodes: SetNodes, setEdges: SetEdges) => boolean;
} {
  const undoStackRef = useRef<Snapshot[]>([]);
  const redoStackRef = useRef<Snapshot[]>([]);

  const pushHistory = useCallback((nodes: Node<RpaNodeData>[], edges: Edge[]): void => {
    undoStackRef.current.push(cloneSnapshot(nodes, edges));
    if (undoStackRef.current.length > MAX_HISTORY) {
      undoStackRef.current.shift();
    }
    // 新操作让原有的重做链失效，否则重做会跳到一条已被覆盖的分支上
    redoStackRef.current = [];
  }, []);

  // 撤销/重做互为镜像：弹出目标栈的快照并把当前画布压入另一侧
  const step = useCallback(
    (from: Snapshot[], to: Snapshot[], current: Snapshot, setNodes: SetNodes, setEdges: SetEdges): boolean => {
      const snapshot = from.pop();
      if (snapshot === undefined) return false;
      to.push(cloneSnapshot(current.nodes, current.edges));
      if (to.length > MAX_HISTORY) {
        to.shift();
      }
      setNodes(snapshot.nodes);
      setEdges(snapshot.edges);
      return true;
    },
    []
  );

  const undo = useCallback(
    (current: Snapshot, setNodes: SetNodes, setEdges: SetEdges): boolean =>
      step(undoStackRef.current, redoStackRef.current, current, setNodes, setEdges),
    [step]
  );

  const redo = useCallback(
    (current: Snapshot, setNodes: SetNodes, setEdges: SetEdges): boolean =>
      step(redoStackRef.current, undoStackRef.current, current, setNodes, setEdges),
    [step]
  );

  return { pushHistory, undo, redo };
}
