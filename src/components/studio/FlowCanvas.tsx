import {
  Background,
  BackgroundVariant,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useViewport,
  type Edge,
  type Connection,
  type OnEdgesChange,
  type OnNodesChange,
  type Node,
  type XYPosition
} from '@xyflow/react';
import type { DragEvent, MouseEvent as ReactMouseEvent, ReactElement } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';

import { cn } from '../../lib/utils';
import { canConnectEdge, parseComponentDragPayload, type ComponentDragPayload } from '../../lib/flowOperations';
import { mergeRuntimeVariables } from '../../lib/runtimeVariables';
import { validateNodeConfigurationInFlow } from '../../lib/runValidation';
import { useCanvasShortcuts } from '../../hooks/useCanvasShortcuts';
import type { CanvasToolMode, ContextMenuAction, ContextMenuState, NodeRuntimeState, RpaNodeData, RuntimeProgress, RuntimeVariable } from '../../types/rpa';
import { CanvasToolbar } from './CanvasToolbar';
import { ContextMenu } from './ContextMenu';
import { nodeTypes } from './FlowNodes';

export function FlowCanvas({
  aiPanelOpen,
  bottomPanelOpen,
  focusNodeRequest,
  flowEdges,
  flowNodes,
  hasMissingStartEnd,
  inputVariables,
  onAddNode,
  onConnectNodes,
  onContextAction,
  nodeStates,
  onEdgesChange,
  onNodesChange,
  onRestoreStartEnd,
  progress,
  onToggleAiPanel,
  onToggleBottomPanel,
  selectedNodeId,
  onSelectedNodeChange
}: {
  aiPanelOpen: boolean;
  bottomPanelOpen: boolean;
  focusNodeRequest: { id: number; nodeId: string } | null;
  flowEdges: Edge[];
  flowNodes: Node<RpaNodeData>[];
  hasMissingStartEnd: boolean;
  inputVariables: RuntimeVariable[];
  onAddNode: (payload: ComponentDragPayload, position: XYPosition) => void;
  onConnectNodes: (edge: Edge) => void;
  onContextAction: (action: ContextMenuAction, nodeId: string) => void;
  nodeStates: Record<string, NodeRuntimeState>;
  onEdgesChange: OnEdgesChange<Edge>;
  onNodesChange: OnNodesChange<Node<RpaNodeData>>;
  onRestoreStartEnd: () => void;
  progress: RuntimeProgress;
  onToggleAiPanel: () => void;
  onToggleBottomPanel: () => void;
  selectedNodeId: string;
  onSelectedNodeChange: (nodeId: string) => void;
}): ReactElement {
  return (
    <ReactFlowProvider>
      <FlowCanvasInner
        aiPanelOpen={aiPanelOpen}
        bottomPanelOpen={bottomPanelOpen}
        focusNodeRequest={focusNodeRequest}
        flowEdges={flowEdges}
        flowNodes={flowNodes}
        hasMissingStartEnd={hasMissingStartEnd}
        inputVariables={inputVariables}
        nodeStates={nodeStates}
        onAddNode={onAddNode}
        onConnectNodes={onConnectNodes}
        onContextAction={onContextAction}
        onEdgesChange={onEdgesChange}
        onNodesChange={onNodesChange}
        onRestoreStartEnd={onRestoreStartEnd}
        progress={progress}
        onSelectedNodeChange={onSelectedNodeChange}
        onToggleAiPanel={onToggleAiPanel}
        onToggleBottomPanel={onToggleBottomPanel}
        selectedNodeId={selectedNodeId}
      />
    </ReactFlowProvider>
  );
}

function FlowCanvasInner({
  aiPanelOpen,
  bottomPanelOpen,
  focusNodeRequest,
  flowEdges,
  flowNodes,
  hasMissingStartEnd,
  inputVariables,
  nodeStates,
  onAddNode,
  onConnectNodes,
  onContextAction,
  onEdgesChange,
  onNodesChange,
  onRestoreStartEnd,
  progress,
  onToggleAiPanel,
  onToggleBottomPanel,
  selectedNodeId,
  onSelectedNodeChange
}: {
  aiPanelOpen: boolean;
  bottomPanelOpen: boolean;
  focusNodeRequest: { id: number; nodeId: string } | null;
  flowEdges: Edge[];
  flowNodes: Node<RpaNodeData>[];
  hasMissingStartEnd: boolean;
  inputVariables: RuntimeVariable[];
  nodeStates: Record<string, NodeRuntimeState>;
  onAddNode: (payload: ComponentDragPayload, position: XYPosition) => void;
  onConnectNodes: (edge: Edge) => void;
  onContextAction: (action: ContextMenuAction, nodeId: string) => void;
  onEdgesChange: OnEdgesChange<Edge>;
  onNodesChange: OnNodesChange<Node<RpaNodeData>>;
  onRestoreStartEnd: () => void;
  progress: RuntimeProgress;
  onToggleAiPanel: () => void;
  onToggleBottomPanel: () => void;
  selectedNodeId: string;
  onSelectedNodeChange: (nodeId: string) => void;
}): ReactElement {
  const [contextMenu, setContextMenu] = useState<ContextMenuState>(null);
  const [gridVisible, setGridVisible] = useState(true);
  const [mode, setMode] = useState<CanvasToolMode>('pan');
  const canvasRef = useRef<HTMLDivElement>(null);
  const handledFocusRequestRef = useRef<number | null>(null);
  const initialFitDoneRef = useRef(false);
  const lastAutoFitSignatureRef = useRef('');
  const { screenToFlowPosition, setViewport, getViewport, fitView } = useReactFlow<Node<RpaNodeData>, Edge>();
  const viewport = useViewport();
  const availableVariableNames = useMemo(() => mergeRuntimeVariables(inputVariables, []).map((variable) => variable.name), [inputVariables]);

  const visibleNodes = useMemo(
    () =>
      flowNodes.map((node) => {
        const validationIssues = validateNodeConfigurationInFlow(node, flowNodes, flowEdges, availableVariableNames);
        const validationSeverity: RpaNodeData['validationSeverity'] =
          validationIssues.some((issue) => issue.severity === 'error')
            ? 'error'
            : validationIssues.length > 0
              ? 'warn'
              : undefined;
        const runtimeState = nodeStates[node.id];
        if (runtimeState === undefined) {
          return {
            ...node,
            data: {
              ...node.data,
              onAction: (action: ContextMenuAction) => onContextAction(action, node.id),
              validationCount: validationIssues.length > 0 ? validationIssues.length : undefined,
              validationSeverity
            },
            selected: node.id === selectedNodeId
          };
        }
        return {
          ...node,
          data: {
            ...node.data,
            badge: runtimeState.badge ?? (runtimeState.status === node.data.status ? node.data.badge : undefined),
            onAction: (action: ContextMenuAction) => onContextAction(action, node.id),
            status: runtimeState.status,
            validationCount: validationIssues.length > 0 ? validationIssues.length : undefined,
            validationSeverity
          },
          selected: node.id === selectedNodeId
        };
      }),
    [availableVariableNames, flowEdges, flowNodes, nodeStates, onContextAction, selectedNodeId]
  );

  const visibleEdges = useMemo(() => {
    return flowEdges.map((edge) => {
      const targetNodeState = nodeStates[edge.target];
      const isRunning = targetNodeState?.status === 'running';
      if (edge.selected) {
        return {
          ...edge,
          style: { stroke: '#3733e6', strokeWidth: 2.5 },
          markerEnd: { type: MarkerType.ArrowClosed, color: '#3733e6' },
          className: cn(edge.className, isRunning && 'edge-running')
        };
      }
      if (isRunning) {
        return { ...edge, className: cn(edge.className, 'edge-running') };
      }
      return edge;
    });
  }, [flowEdges, nodeStates]);

  const toolbarStats = useMemo(
    () => ({
      doneSteps: visibleNodes.filter((node) => node.data.status === 'done' && node.id !== 'start' && node.id !== 'end').length,
      runningSteps: visibleNodes.filter((node) => node.data.status === 'running').length,
      totalSteps: visibleNodes.filter((node) => node.id !== 'start' && node.id !== 'end').length
    }),
    [visibleNodes]
  );
  const contextMenuNode = flowNodes.find((node) => node.id === (contextMenu?.nodeId ?? selectedNodeId));
  const flowLayoutSignature = useMemo(
    () => JSON.stringify({
      edges: flowEdges.map((edge) => `${edge.source}->${edge.target}:${typeof edge.label === 'string' ? edge.label : ''}`).sort(),
      nodes: flowNodes.map((node) => `${node.id}:${Math.round(node.position.x)},${Math.round(node.position.y)}`).sort()
    }),
    [flowEdges, flowNodes]
  );

  const findNodeAtPoint = (clientX: number, clientY: number): Node<RpaNodeData> | undefined => {
    const nodeElements = Array.from(canvasRef.current?.querySelectorAll<HTMLElement>('.react-flow__node[data-id]') ?? []);
    const nodeElement = nodeElements
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        return clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom;
      })
      .sort((left, right) => {
        const leftZIndex = Number.parseInt(window.getComputedStyle(left).zIndex, 10);
        const rightZIndex = Number.parseInt(window.getComputedStyle(right).zIndex, 10);
        return (Number.isNaN(rightZIndex) ? 0 : rightZIndex) - (Number.isNaN(leftZIndex) ? 0 : leftZIndex);
      })[0];

    const nodeId = nodeElement?.dataset.id;
    return flowNodes.find((item) => item.id === nodeId);
  };

  const handleContextMenuCapture = (event: ReactMouseEvent<HTMLDivElement>): void => {
    const node = findNodeAtPoint(event.clientX, event.clientY);
    if (node === undefined) {
      setContextMenu(null);
      event.preventDefault();
      event.stopPropagation();
      return;
    }

    onSelectedNodeChange(node.id);
    setContextMenu({ nodeId: node.id, nodeTitle: node.data.title });
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>): void => {
    event.preventDefault();
    const payload = parseComponentDragPayload(event.dataTransfer.getData('application/rpa-node'));
    if (payload === null) {
      return;
    }
    onAddNode(payload, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
  };

  const handleConnect = (connection: Connection): void => {
    if (!canConnectEdge(flowEdges, connection.source, connection.target)) {
      return;
    }
    onConnectNodes({ id: '', source: connection.source, target: connection.target } as Edge);
  };

  const setViewportZoom = (nextZoom: number): void => {
    const safeZoom = Math.min(1.6, Math.max(0.2, nextZoom));
    const current = getViewport();
    const rect = canvasRef.current?.getBoundingClientRect();
    if (rect === undefined) {
      setViewport({ ...current, zoom: safeZoom });
      return;
    }

    const centerX = rect.width / 2;
    const centerY = rect.height / 2;
    const flowCenterX = (centerX - current.x) / current.zoom;
    const flowCenterY = (centerY - current.y) / current.zoom;

    setViewport({
      x: centerX - flowCenterX * safeZoom,
      y: centerY - flowCenterY * safeZoom,
      zoom: safeZoom
    });
  };

  const handleFitView = (): void => {
    const canvasRect = canvasRef.current?.getBoundingClientRect();
    const nodeElements = Array.from(canvasRef.current?.querySelectorAll<HTMLElement>('.react-flow__node[data-id]') ?? []);
    if (canvasRect === undefined || nodeElements.length === 0) {
      return;
    }

    const { x: vpX, y: vpY, zoom: vpZoom } = getViewport();
    const bounds = nodeElements.reduce(
      (acc, element) => {
        const rect = element.getBoundingClientRect();
        const left = (rect.left - canvasRect.left - vpX) / vpZoom;
        const top = (rect.top - canvasRect.top - vpY) / vpZoom;
        const right = (rect.right - canvasRect.left - vpX) / vpZoom;
        const bottom = (rect.bottom - canvasRect.top - vpY) / vpZoom;
        return {
          bottom: Math.max(acc.bottom, bottom),
          left: Math.min(acc.left, left),
          right: Math.max(acc.right, right),
          top: Math.min(acc.top, top)
        };
      },
      { bottom: Number.NEGATIVE_INFINITY, left: Number.POSITIVE_INFINITY, right: Number.NEGATIVE_INFINITY, top: Number.POSITIVE_INFINITY }
    );

    const padding = 0.26;
    const boundsWidth = Math.max(1, bounds.right - bounds.left);
    const boundsHeight = Math.max(1, bounds.bottom - bounds.top);
    const nextZoom = Math.min(1.6, Math.max(0.2, Math.min(canvasRect.width / (boundsWidth * (1 + padding)), canvasRect.height / (boundsHeight * (1 + padding)))));

    setViewport({
      x: (canvasRect.width - boundsWidth * nextZoom) / 2 - bounds.left * nextZoom,
      y: (canvasRect.height - boundsHeight * nextZoom) / 2 - bounds.top * nextZoom,
      zoom: nextZoom
    });
  };

  const handleZoomIn = (): void => {
    setViewportZoom(viewport.zoom * 1.2);
  };

  const handleZoomOut = (): void => {
    setViewportZoom(viewport.zoom / 1.2);
  };

  const handleResetZoom = (): void => {
    setViewportZoom(1);
  };

  useCanvasShortcuts({
    mode,
    onFitView: handleFitView,
    onModeChange: setMode,
    onResetZoom: handleResetZoom,
    onZoomIn: handleZoomIn,
    onZoomOut: handleZoomOut
  });

  // Auto-center when nodes are first loaded. useEffect fires after paint so
  // nodes are already in the DOM; rAF defers one more frame to let ReactFlow
  // finish its own layout measurement before we read positions.
  useEffect(() => {
    if (initialFitDoneRef.current || flowNodes.length === 0) return;
    initialFitDoneRef.current = true;
    lastAutoFitSignatureRef.current = flowLayoutSignature;
    const id = requestAnimationFrame(() => fitView({ padding: 0.18, maxZoom: 0.9 }));
    return () => cancelAnimationFrame(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flowNodes.length]);

  // AI create/update writes can replace the whole graph layout after the canvas
  // has already loaded. Re-fit once per structural layout signature so the
  // normalized topology is immediately visible instead of being scattered off
  // the current viewport.
  useEffect(() => {
    if (!initialFitDoneRef.current || flowNodes.length === 0) return;
    if (lastAutoFitSignatureRef.current === flowLayoutSignature) return;
    lastAutoFitSignatureRef.current = flowLayoutSignature;
    const id = requestAnimationFrame(() => fitView({ padding: 0.2, maxZoom: 0.95 }));
    return () => cancelAnimationFrame(id);
  }, [fitView, flowLayoutSignature, flowNodes.length]);

  useEffect(() => {
    if (focusNodeRequest === null || handledFocusRequestRef.current === focusNodeRequest.id) {
      return;
    }

    const selectedNode = flowNodes.find((node) => node.id === focusNodeRequest.nodeId);
    const rect = canvasRef.current?.getBoundingClientRect();
    if (selectedNode === undefined || rect === undefined) {
      return;
    }

    handledFocusRequestRef.current = focusNodeRequest.id;
    const nodeWidth = selectedNode.measured?.width ?? selectedNode.width ?? 240;
    const nodeHeight = selectedNode.measured?.height ?? selectedNode.height ?? (selectedNode.type === 'startEnd' ? 32 : 96);
    const centerX = selectedNode.position.x + nodeWidth / 2;
    const centerY = selectedNode.position.y + nodeHeight / 2;
    const current = getViewport();
    setViewport({
      ...current,
      x: rect.width / 2 - centerX * current.zoom,
      y: rect.height / 2 - centerY * current.zoom
    });
  }, [flowNodes, focusNodeRequest]);

  return (
    <main className="flex min-w-0 flex-1 flex-col" style={{ background: '#f3f4f8' }}>
      <CanvasToolbar
        aiPanelOpen={aiPanelOpen}
        bottomPanelOpen={bottomPanelOpen}
        gridVisible={gridVisible}
        hasMissingStartEnd={hasMissingStartEnd}
        mode={mode}
        onFitView={handleFitView}
        onModeChange={setMode}
        onRestoreStartEnd={onRestoreStartEnd}
        onToggleAiPanel={onToggleAiPanel}
        onToggleBottomPanel={onToggleBottomPanel}
        onToggleGrid={() => setGridVisible((visible) => !visible)}
        onZoomIn={handleZoomIn}
        onZoomOut={handleZoomOut}
        progress={progress}
        stats={toolbarStats}
        zoom={viewport.zoom}
      />
      <ContextMenu
        hasBreakpoint={contextMenuNode?.data.breakpoint === true}
        nodeId={contextMenu?.nodeId ?? selectedNodeId}
        nodeTitle={contextMenu?.nodeTitle ?? '未选择节点'}
        onAction={(action, nodeId) => {
          onContextAction(action, nodeId);
          setContextMenu(null);
        }}
        onOpenChange={(open) => {
          if (!open) {
            setContextMenu(null);
          }
        }}
      >
        <div
          className="relative min-h-0 flex-1"
          onContextMenuCapture={handleContextMenuCapture}
          onDragOver={(event) => event.preventDefault()}
          onDrop={handleDrop}
          ref={canvasRef}
        >
          <ReactFlow
            edges={visibleEdges}
            nodes={visibleNodes}
            nodeTypes={nodeTypes}
            nodesDraggable={mode === 'select'}
            deleteKeyCode={null}
            onConnect={handleConnect}
            onEdgesChange={onEdgesChange}
            onNodeClick={(_, node) => {
              onSelectedNodeChange(node.id);
              setContextMenu(null);
            }}
            onNodesChange={onNodesChange}
            onPaneClick={() => { setContextMenu(null); onSelectedNodeChange(''); }}
            panOnDrag={mode === 'pan'}
            proOptions={{ hideAttribution: true }}
            selectionOnDrag={mode === 'select'}
            snapGrid={[10, 10]}
            snapToGrid
          >
            {gridVisible && <Background color="#c6cdd9" gap={22} size={1.4} variant={BackgroundVariant.Dots} />}
            <MiniMap
              maskColor="rgba(248,250,252,0.82)"
              nodeBorderRadius={5}
              nodeColor={(node) => {
                const status = (node.data as RpaNodeData | undefined)?.status;
                if (status === 'done') return '#10b981';
                if (status === 'running') return '#3733e6';
                if (status === 'error') return '#ef4444';
                if (status === 'skipped') return '#cbd5e1';
                return '#dde1ea';
              }}
              nodeStrokeWidth={0}
              pannable
              style={{
                backgroundColor: '#f8fafc',
                borderRadius: 10,
                overflow: 'hidden',
                boxShadow: '0 2px 10px rgba(15,23,42,0.10), 0 0 0 1px rgba(226,232,240,0.7)',
              }}
              zoomable
            />
          </ReactFlow>
        </div>
      </ContextMenu>
    </main>
  );
}
