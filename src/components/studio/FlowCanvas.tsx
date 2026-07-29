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
import { canConnectEdge, NODE_SIZE, parseComponentDragPayload, type ComponentDragPayload } from '../../lib/flowOperations';
import { mergeRuntimeVariables } from '../../lib/runtimeVariables';
import { validateFlowConfigurations } from '../../lib/runValidation';
import { useCanvasShortcuts } from '../../hooks/useCanvasShortcuts';
import type { CanvasToolMode, ContextMenuAction, ContextMenuState, NodeRuntimeState, RpaNodeData, RuntimeProgress, RuntimeVariable } from '../../types/rpa';
import { CanvasToolbar } from './canvas-toolbar/CanvasToolbar';
import { ContextMenu } from './ContextMenu';
import { nodeTypes } from './FlowNodes';

export function FlowCanvas({
  bottomPanelOpen,
  canvasFitVersion,
  focusMode,
  focusNodeRequest,
  flowEdges,
  flowNodes,
  hasMissingStartEnd,
  inputVariables,
  onAddNode,
  onBeginNodeDrag,
  onEndNodeDrag,
  onConnectNodes,
  onContextAction,
  nodeStates,
  onEdgesChange,
  onNodesChange,
  onRestoreStartEnd,
  progress,
  onToggleBottomPanel,
  onToggleFocusMode,
  selectedNodeId,
  onSelectedNodeChange
}: {
  bottomPanelOpen: boolean;
  canvasFitVersion: number;
  focusMode: boolean;
  focusNodeRequest: { id: number; nodeId: string } | null;
  flowEdges: Edge[];
  flowNodes: Node<RpaNodeData>[];
  hasMissingStartEnd: boolean;
  inputVariables: RuntimeVariable[];
  onAddNode: (payload: ComponentDragPayload, position: XYPosition) => void;
  onBeginNodeDrag: () => void;
  onEndNodeDrag: () => void;
  onConnectNodes: (edge: Edge) => void;
  onContextAction: (action: ContextMenuAction, nodeId: string) => void;
  nodeStates: Record<string, NodeRuntimeState>;
  onEdgesChange: OnEdgesChange<Edge>;
  onNodesChange: OnNodesChange<Node<RpaNodeData>>;
  onRestoreStartEnd: () => void;
  progress: RuntimeProgress;
  onToggleBottomPanel: () => void;
  onToggleFocusMode: () => void;
  selectedNodeId: string;
  onSelectedNodeChange: (nodeId: string) => void;
}): ReactElement {
  return (
    <ReactFlowProvider>
      <FlowCanvasInner
        bottomPanelOpen={bottomPanelOpen}
        canvasFitVersion={canvasFitVersion}
        focusMode={focusMode}
        focusNodeRequest={focusNodeRequest}
        flowEdges={flowEdges}
        flowNodes={flowNodes}
        hasMissingStartEnd={hasMissingStartEnd}
        inputVariables={inputVariables}
        nodeStates={nodeStates}
        onAddNode={onAddNode}
        onBeginNodeDrag={onBeginNodeDrag}
        onEndNodeDrag={onEndNodeDrag}
        onConnectNodes={onConnectNodes}
        onContextAction={onContextAction}
        onEdgesChange={onEdgesChange}
        onNodesChange={onNodesChange}
        onRestoreStartEnd={onRestoreStartEnd}
        progress={progress}
        onSelectedNodeChange={onSelectedNodeChange}
        onToggleBottomPanel={onToggleBottomPanel}
        onToggleFocusMode={onToggleFocusMode}
        selectedNodeId={selectedNodeId}
      />
    </ReactFlowProvider>
  );
}

function FlowCanvasInner({
  bottomPanelOpen,
  canvasFitVersion,
  focusMode,
  focusNodeRequest,
  flowEdges,
  flowNodes,
  hasMissingStartEnd,
  inputVariables,
  nodeStates,
  onAddNode,
  onBeginNodeDrag,
  onEndNodeDrag,
  onConnectNodes,
  onContextAction,
  onEdgesChange,
  onNodesChange,
  onRestoreStartEnd,
  progress,
  onToggleBottomPanel,
  onToggleFocusMode,
  selectedNodeId,
  onSelectedNodeChange
}: {
  bottomPanelOpen: boolean;
  canvasFitVersion: number;
  focusMode: boolean;
  focusNodeRequest: { id: number; nodeId: string } | null;
  flowEdges: Edge[];
  flowNodes: Node<RpaNodeData>[];
  hasMissingStartEnd: boolean;
  inputVariables: RuntimeVariable[];
  nodeStates: Record<string, NodeRuntimeState>;
  onAddNode: (payload: ComponentDragPayload, position: XYPosition) => void;
  onBeginNodeDrag: () => void;
  onEndNodeDrag: () => void;
  onConnectNodes: (edge: Edge) => void;
  onContextAction: (action: ContextMenuAction, nodeId: string) => void;
  onEdgesChange: OnEdgesChange<Edge>;
  onNodesChange: OnNodesChange<Node<RpaNodeData>>;
  onRestoreStartEnd: () => void;
  progress: RuntimeProgress;
  onToggleBottomPanel: () => void;
  onToggleFocusMode: () => void;
  selectedNodeId: string;
  onSelectedNodeChange: (nodeId: string) => void;
}): ReactElement {
  const [contextMenu, setContextMenu] = useState<ContextMenuState>(null);
  const [gridVisible, setGridVisible] = useState(true);
  const [miniMapVisible, setMiniMapVisible] = useState(true);
  const [mode, setMode] = useState<CanvasToolMode>('pan');
  const canvasRef = useRef<HTMLDivElement>(null);
  const handledFocusRequestRef = useRef<number | null>(null);
  const initialFitDoneRef = useRef(false);
  const lastAutoFitSignatureRef = useRef('');
  const lastFitNodeIdsRef = useRef<string[]>([]);
  const { screenToFlowPosition, setViewport, getViewport, fitView } = useReactFlow<Node<RpaNodeData>, Edge>();
  const viewport = useViewport();
  const availableVariableNames = useMemo(() => mergeRuntimeVariables(inputVariables, []).map((variable) => variable.name), [inputVariables]);

  // 校验只取决于流程结构，与运行时状态无关：单独 memo，避免每个进度事件重跑全流程校验
  const validationByNodeId = useMemo(
    () => validateFlowConfigurations(flowNodes, flowEdges, availableVariableNames),
    [availableVariableNames, flowEdges, flowNodes]
  );

  // 基础节点：只随流程结构变化重建，运行时状态变化时保持 data 引用不变，
  // 这样没有运行态的节点在整轮运行里一次都不会重渲染
  const baseNodes = useMemo(
    () =>
      flowNodes.map((node) => {
        const validationIssues = validationByNodeId.get(node.id);
        const hasIssues = validationIssues !== undefined && validationIssues.length > 0;
        return {
          ...node,
          // 选中态一律由 selectedNodeId 决定，不沿用 flowNodes 上 ReactFlow 自己写入的 selected
          selected: false,
          data: {
            ...node.data,
            onAction: (action: ContextMenuAction) => onContextAction(action, node.id),
            validationCount: hasIssues ? validationIssues.length : undefined,
            validationSeverity: (!hasIssues
              ? undefined
              : validationIssues.some((issue) => issue.severity === 'error')
                ? 'error'
                : 'warn') as RpaNodeData['validationSeverity']
          }
        };
      }),
    [flowNodes, onContextAction, validationByNodeId]
  );

  const visibleNodes = useMemo(
    () =>
      baseNodes.map((node) => {
        const runtimeState = nodeStates[node.id];
        const selected = node.id === selectedNodeId;
        if (runtimeState === undefined) {
          return selected ? { ...node, selected: true } : node;
        }
        return {
          ...node,
          data: {
            ...node.data,
            badge: runtimeState.badge ?? (runtimeState.status === node.data.status ? node.data.badge : undefined),
            status: runtimeState.status
          },
          selected
        };
      }),
    [baseNodes, nodeStates, selectedNodeId]
  );

  const visibleEdges = useMemo(() => {
    return flowEdges.map((edge) => {
      const targetNodeState = nodeStates[edge.target];
      const isRunning = targetNodeState?.status === 'running';
      if (edge.selected) {
        return {
          ...edge,
          style: { stroke: '#6366f1', strokeWidth: 2.5 },
          markerEnd: { type: MarkerType.ArrowClosed, color: '#6366f1' },
          className: cn(edge.className, isRunning && 'edge-running')
        };
      }
      if (isRunning) {
        return { ...edge, className: cn(edge.className, 'edge-running') };
      }
      return edge;
    });
  }, [flowEdges, nodeStates]);

  const toolbarStats = useMemo(() => {
    let doneSteps = 0;
    let runningSteps = 0;
    let totalSteps = 0;
    for (const node of visibleNodes) {
      if (node.data.status === 'running') runningSteps += 1;
      if (node.id === 'start' || node.id === 'end') continue;
      totalSteps += 1;
      if (node.data.status === 'done') doneSteps += 1;
    }
    return { doneSteps, runningSteps, totalSteps };
  }, [visibleNodes]);
  const contextMenuNode = flowNodes.find((node) => node.id === (contextMenu?.nodeId ?? selectedNodeId));
  // 只描述结构（节点集合 + 连线），不含坐标：坐标一起算会让拖动节点也触发自动 fitView，视口被拽回去
  const flowLayoutSignature = useMemo(
    () => [
      flowNodes.map((node) => node.id).sort().join(','),
      flowEdges.map((edge) => `${edge.source}>${edge.target}:${typeof edge.label === 'string' ? edge.label : ''}`).sort().join(',')
    ].join('|'),
    [flowEdges, flowNodes]
  );

  // 用 DOM 命中测试而非坐标换算：节点可能重叠，需按渲染 z-index 取最上层的一个
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
    // 落点按节点中心对齐光标：默认换算给的是左上角，视觉上节点会整体偏右下
    const dropPoint = screenToFlowPosition({ x: event.clientX, y: event.clientY });
    onAddNode(payload, { x: dropPoint.x - NODE_SIZE.width / 2, y: dropPoint.y - NODE_SIZE.height / 2 });
  };

  // 交给 ReactFlow 在连线拖拽过程中实时判定，落点非法时不会亮起可连接高亮
  const isValidConnection = (connection: Connection | Edge): boolean =>
    canConnectEdge(flowEdges, connection.source, connection.target);

  const handleConnect = (connection: Connection): void => {
    if (!canConnectEdge(flowEdges, connection.source, connection.target)) {
      return;
    }
    onConnectNodes({ id: '', source: connection.source, target: connection.target } as Edge);
  };

  // 缩放范围钳制在 20%~160%：低于 20% 节点文字不可辨认，高于 160% 画布易失去操作手感
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

  // 不用库自带 fitView：它依赖节点 measured 尺寸，节点刚挂载未稳定时会算错，这里改读实际 DOM rect
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
    onToggleFocusMode,
    onToggleGrid: () => setGridVisible((visible) => !visible),
    onToggleMiniMap: () => setMiniMapVisible((visible) => !visible),
    onZoomIn: handleZoomIn,
    onZoomOut: handleZoomOut
  });

  // 首次加载自动居中；rAF 多等一帧让 ReactFlow 完成自己的布局测量再读取节点位置
  useEffect(() => {
    if (initialFitDoneRef.current || flowNodes.length === 0) return;
    initialFitDoneRef.current = true;
    lastAutoFitSignatureRef.current = flowLayoutSignature;
    lastFitNodeIdsRef.current = flowNodes.map((node) => node.id);
    const id = requestAnimationFrame(() => fitView({ padding: 0.18, maxZoom: 0.9 }));
    return () => cancelAnimationFrame(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flowNodes.length]);

  // AI create/update 可能在画布已加载后整体替换布局；这种情况才重新 fit，避免节点散落在视口外。
  // 增删单个节点属于增量编辑：留住用户当前的视口与缩放，否则每加一个节点画布都会被拉回全局视图
  useEffect(() => {
    if (!initialFitDoneRef.current || flowNodes.length === 0) return;
    if (lastAutoFitSignatureRef.current === flowLayoutSignature) return;
    lastAutoFitSignatureRef.current = flowLayoutSignature;

    const currentNodeIds = new Set(flowNodes.map((node) => node.id));
    const previousNodeIds = lastFitNodeIdsRef.current;
    lastFitNodeIdsRef.current = flowNodes.map((node) => node.id);

    // 保留了半数以上的旧节点就当作增量编辑；整体被换掉（如 AI 新建流程）才重新框选
    const retainedCount = previousNodeIds.filter((nodeId) => currentNodeIds.has(nodeId)).length;
    if (previousNodeIds.length > 0 && retainedCount * 2 >= previousNodeIds.length) return;

    const id = requestAnimationFrame(() => fitView({ padding: 0.2, maxZoom: 0.95 }));
    return () => cancelAnimationFrame(id);
  }, [fitView, flowLayoutSignature, flowNodes]);

  // 冷启动画布恢复（如刷新页面且无草稿）时的显式 fit 请求，只框定流程顶部让开始节点立即可见
  useEffect(() => {
    if (canvasFitVersion === 0) return;
    const id = window.setTimeout(() => {
      const topNodes = [...flowNodes]
        .sort((a, b) => a.position.y - b.position.y)
        .slice(0, 3)
        .map((n) => ({ id: n.id }));
      fitView({ nodes: topNodes.length > 0 ? topNodes : undefined, padding: 0.35, maxZoom: 1.0 });
    }, 80);
    return () => window.clearTimeout(id);
  // flowNodes 故意不列入依赖：快照只在触发时刻取一次
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canvasFitVersion, fitView]);

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
    <main className="flex min-w-0 flex-1 flex-col bg-canvas">
      <CanvasToolbar
        bottomPanelOpen={bottomPanelOpen}
        focusMode={focusMode}
        gridVisible={gridVisible}
        hasMissingStartEnd={hasMissingStartEnd}
        miniMapVisible={miniMapVisible}
        mode={mode}
        onFitView={handleFitView}
        onModeChange={setMode}
        onResetZoom={handleResetZoom}
        onRestoreStartEnd={onRestoreStartEnd}
        onToggleBottomPanel={onToggleBottomPanel}
        onToggleFocusMode={onToggleFocusMode}
        onToggleGrid={() => setGridVisible((visible) => !visible)}
        onToggleMiniMap={() => setMiniMapVisible((visible) => !visible)}
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
            isValidConnection={isValidConnection}
            onConnect={handleConnect}
            onNodeDragStart={onBeginNodeDrag}
            onNodeDragStop={onEndNodeDrag}
            onEdgesChange={onEdgesChange}
            onNodeClick={(_, node) => {
              onSelectedNodeChange(node.id);
              setContextMenu(null);
            }}
            onNodesChange={onNodesChange}
            onPaneClick={() => { setContextMenu(null); onSelectedNodeChange(''); }}
            // 选择模式下保留中键拖拽平移（不含右键，右键要留给节点菜单），不必为了挪一下视图去切工具
            panOnDrag={mode === 'pan' ? true : [1]}
            proOptions={{ hideAttribution: true }}
            selectionOnDrag={mode === 'select'}
            snapGrid={[10, 10]}
            snapToGrid
            minZoom={0.2}
            maxZoom={1.6}
          >
            {gridVisible && <Background color="#c6cdd9" gap={22} size={1.4} variant={BackgroundVariant.Dots} />}
            {miniMapVisible && (
              <MiniMap
                maskColor="rgba(248,250,252,0.82)"
                nodeBorderRadius={5}
                nodeColor={(node) => {
                  const status = (node.data as RpaNodeData | undefined)?.status;
                  if (status === 'done') return '#10b981';
                  if (status === 'running') return '#3b82f6';
                  if (status === 'error') return '#ef4444';
                  if (status === 'skipped') return '#cbd5e1';
                  return '#dde1ea';
                }}
                nodeStrokeWidth={0}
                pannable
                style={{
                  backgroundColor: 'var(--color-canvas)',
                  borderRadius: 10,
                  overflow: 'hidden',
                  boxShadow: '0 2px 10px rgba(15,23,42,0.10), 0 0 0 1px rgba(226,232,240,0.7)',
                }}
                zoomable
              />
            )}
          </ReactFlow>
        </div>
      </ContextMenu>
    </main>
  );
}
