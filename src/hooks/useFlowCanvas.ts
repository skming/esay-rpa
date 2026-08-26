import { useCallback, useMemo, useRef, useState } from 'react';
import {
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type OnNodesChange,
  type XYPosition,
} from '@xyflow/react';
import type { DeleteNodeTarget } from '../components/studio/DeleteNodeDialog';
import { initialEdges, initialNodes } from '../data/studioData';
import { useUndoHistory } from './useUndoHistory';
import {
  createFlowEdge,
  createFlowNode,
  deleteNodeAndReconnect,
  insertNodeAfter,
  summarizeDeleteImpact,
  type ComponentDragPayload,
} from '../lib/flowOperations';
import { usePropertyPanelStore } from '../stores/usePropertyPanelStore';
import type { RpaNodeData } from '../types/rpa';

export function useFlowCanvas() {
  const [storedSelectedNodeId, setSelectedNodeId] = useState('start');
  const [deleteTarget, setDeleteTarget] = useState<DeleteNodeTarget>(null);
  const [focusNodeRequest, setFocusNodeRequest] = useState<{ id: number; nodeId: string } | null>(null);
  const [flowNodes, setFlowNodes, onNodesChangeRaw] = useNodesState(initialNodes);
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState(initialEdges);

  const onNodesChange: OnNodesChange<Node<RpaNodeData>> = useCallback(
    (changes) => {
      onNodesChangeRaw(changes.filter((c) => !(c.type === 'remove' && (c.id === 'start' || c.id === 'end'))));
    },
    [onNodesChangeRaw]
  );

  const { pushHistory, undo: undoHistory, redo: redoHistory } = useUndoHistory();

  const selectedEdgeId = useMemo(() => flowEdges.find((e) => e.selected)?.id ?? null, [flowEdges]);
  // 选中节点被删后 id 会悬空，推导时兜底而非在 effect 里回写 state——回写会先绘一帧属性面板悬空的中间态
  const selectedNode = useMemo(
    () => flowNodes.find((node) => node.id === storedSelectedNodeId) ?? flowNodes[0],
    [flowNodes, storedSelectedNodeId]
  );
  const selectedNodeId = selectedNode?.id ?? 'start';

  const hasMissingStartEnd = useMemo(
    () => !flowNodes.some((n) => n.id === 'start') || !flowNodes.some((n) => n.id === 'end'),
    [flowNodes]
  );

  const restoreStartEnd = useCallback(() => {
    setFlowNodes((nodes) => {
      const hasStart = nodes.some((n) => n.id === 'start');
      const hasEnd = nodes.some((n) => n.id === 'end');
      if (hasStart && hasEnd) return nodes;
      const result = [...nodes];
      if (!hasStart) {
        result.unshift({ ...initialNodes[0], position: { ...initialNodes[0].position }, data: { ...initialNodes[0].data } });
      }
      if (!hasEnd) {
        const last = result[result.length - 1];
        const endY = last !== undefined ? last.position.y + 120 : initialNodes[1].position.y;
        result.push({ ...initialNodes[1], position: { x: initialNodes[1].position.x, y: endY }, data: { ...initialNodes[1].data } });
      }
      return result;
    });
  }, [setFlowNodes]);

  const setPropertyPanelCollapsed = usePropertyPanelStore((state) => state.setCollapsed);
  const setPropertyPanelActiveTab = usePropertyPanelStore((state) => state.setActiveTab);

  const addNodeAtPosition = useCallback(
    (payload: ComponentDragPayload, position: XYPosition): string => {
      pushHistory(flowNodes, flowEdges);
      const node = createFlowNode(payload, position, flowNodes.length + 1);
      setFlowNodes((nodes) => [...nodes, node]);
      setSelectedNodeId(node.id);
      return node.id;
    },
    [flowEdges, flowNodes, pushHistory, setFlowNodes]
  );

  const addNodeAfterSelection = useCallback(
    (payload: ComponentDragPayload): void => {
      const insertion = insertNodeAfter(flowNodes, flowEdges, selectedNodeId, payload);
      if (insertion === null) {
        const selectedPosition = selectedNode?.position ?? { x: 500, y: 500 };
        const nodeId = addNodeAtPosition(payload, { x: selectedPosition.x + 40, y: selectedPosition.y + 120 });
        setFocusNodeRequest((current) => ({ id: (current?.id ?? 0) + 1, nodeId }));
        return;
      }
      pushHistory(flowNodes, flowEdges);
      setFlowNodes((nodes) => [...nodes, insertion.node]);
      setFlowEdges(insertion.edges);
      setSelectedNodeId(insertion.node.id);
      // 画布不再因为新增节点整体重置视图，所以这里主动把新节点平移到视口中央
      setFocusNodeRequest((current) => ({ id: (current?.id ?? 0) + 1, nodeId: insertion.node.id }));
    },
    [addNodeAtPosition, flowEdges, flowNodes, pushHistory, selectedNode, selectedNodeId, setFlowEdges, setFlowNodes]
  );

  const connectNodes = useCallback(
    (edge: Edge): void => {
      pushHistory(flowNodes, flowEdges);
      setFlowEdges((edges) => [...edges, createFlowEdge(edge.source, edge.target)]);
    },
    [flowEdges, flowNodes, pushHistory, setFlowEdges]
  );

  const deleteEdge = useCallback(
    (edgeId: string): void => {
      pushHistory(flowNodes, flowEdges);
      setFlowEdges((edges) => edges.filter((e) => e.id !== edgeId));
    },
    [flowEdges, flowNodes, pushHistory, setFlowEdges]
  );

  const undoAction = useCallback((): void => {
    undoHistory({ nodes: flowNodes, edges: flowEdges }, setFlowNodes, setFlowEdges);
  }, [flowEdges, flowNodes, undoHistory, setFlowNodes, setFlowEdges]);

  const redoAction = useCallback((): void => {
    redoHistory({ nodes: flowNodes, edges: flowEdges }, setFlowNodes, setFlowEdges);
  }, [flowEdges, flowNodes, redoHistory, setFlowNodes, setFlowEdges]);

  // 拖动节点前存档：位置调整也应该能被 ⌘Z 撤销，否则一次误拖只能手动拖回去。
  // 拖拽开始事件在按下鼠标时就会触发，所以先暂存快照，等松手确认位置真的变了再入栈，
  // 否则每次点选节点都会往历史里塞一条空操作，⌘Z 会连按几次都没反应
  const dragSnapshotRef = useRef<{ nodes: Node<RpaNodeData>[]; edges: Edge[] } | null>(null);

  const beginNodeDrag = useCallback((): void => {
    dragSnapshotRef.current = { nodes: flowNodes, edges: flowEdges };
  }, [flowEdges, flowNodes]);

  const endNodeDrag = useCallback((): void => {
    const snapshot = dragSnapshotRef.current;
    dragSnapshotRef.current = null;
    if (snapshot === null) return;
    const movedNode = flowNodes.find((node) => {
      const before = snapshot.nodes.find((item) => item.id === node.id);
      return before !== undefined && (before.position.x !== node.position.x || before.position.y !== node.position.y);
    });
    if (movedNode === undefined) return;
    pushHistory(snapshot.nodes, snapshot.edges);
  }, [flowNodes, pushHistory]);

  const updateNodeData = useCallback(
    (nodeId: string, data: RpaNodeData): void => {
      setFlowNodes((nodes) => nodes.map((node) => (node.id === nodeId ? { ...node, data } : node)));
    },
    [setFlowNodes]
  );

  const focusNode = useCallback(
    (nodeId: string): void => {
      setSelectedNodeId(nodeId);
      setPropertyPanelCollapsed(false);
      setPropertyPanelActiveTab('config');
      setFocusNodeRequest((current) => ({ id: (current?.id ?? 0) + 1, nodeId }));
    },
    [setPropertyPanelActiveTab, setPropertyPanelCollapsed]
  );

  const updateNodeBreakpoint = useCallback(
    (nodeId: string, enabled: boolean): void => {
      setFlowNodes((nodes) =>
        nodes.map((node) => (node.id === nodeId ? { ...node, data: { ...node.data, breakpoint: enabled } } : node))
      );
    },
    [setFlowNodes]
  );

  const requestDeleteNode = useCallback(
    (nodeId: string): void => {
      if (nodeId === 'start' || nodeId === 'end') return;
      const target = flowNodes.find((node) => node.id === nodeId);
      if (target === undefined) return;
      setDeleteTarget({
        id: target.id,
        title: target.data.title,
        impact: summarizeDeleteImpact(flowNodes, flowEdges, nodeId),
      });
    },
    [flowEdges, flowNodes]
  );

  const confirmDeleteNode = useCallback((): void => {
    if (deleteTarget === null) return;
    pushHistory(flowNodes, flowEdges);
    const nodeId = deleteTarget.id;
    setFlowEdges((edges) => deleteNodeAndReconnect(flowNodes, edges, nodeId));
    setFlowNodes((nodes) => nodes.filter((node) => node.id !== nodeId));
    if (selectedNodeId === nodeId) setSelectedNodeId('start');
    setDeleteTarget(null);
  }, [deleteTarget, flowEdges, flowNodes, pushHistory, selectedNodeId, setFlowEdges, setFlowNodes]);

  return {
    flowNodes, setFlowNodes,
    flowEdges, setFlowEdges,
    onNodesChange, onEdgesChange,
    selectedNodeId, setSelectedNodeId,
    selectedNode,
    selectedEdgeId,
    hasMissingStartEnd, restoreStartEnd,
    deleteTarget, setDeleteTarget,
    focusNodeRequest,
    addNodeAtPosition, addNodeAfterSelection,
    connectNodes, deleteEdge, undoAction, redoAction, beginNodeDrag, endNodeDrag,
    updateNodeData, focusNode, updateNodeBreakpoint,
    requestDeleteNode, confirmDeleteNode,
  };
}
