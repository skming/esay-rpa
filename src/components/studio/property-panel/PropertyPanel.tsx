import { Check, CheckCircle2, Copy, Loader2, PanelRightClose, PanelRightOpen, RotateCcw, Save, TriangleAlert } from 'lucide-react';
import type { Node } from '@xyflow/react';
import type { ReactElement } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';

import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import { applyNodeConfigDraft, createNodeConfigDraft } from '../../../lib/nodeConfigDraft';
import { usePropertyPanelStore } from '../../../stores/usePropertyPanelStore';
import type { PanelTab, RpaNodeConfigDraft, RpaNodeData, RuntimeVariable } from '../../../types/rpa';
import { Button, IconButton } from '../../ui/button';
import { Tabs, TabsList, TabsTrigger } from '../../ui/tabs';
import { cn } from '../../../lib/utils';
import { AdvancedTab } from './AdvancedTab';
import { PropertyContent } from './PropertyContent';
import { PropertyEmptyState } from './PropertyEmptyState';

export function PropertyPanel({
  electron,
  flowEdges,
  flowNodes,
  inputVariables,
  onUpdateNodeData,
  selectedNode
}: {
  electron: ElectronBridgeState;
  flowEdges: import('@xyflow/react').Edge[];
  flowNodes: Node<RpaNodeData>[];
  inputVariables: RuntimeVariable[];
  onUpdateNodeData: (nodeId: string, data: RpaNodeData) => void;
  selectedNode: Node<RpaNodeData> | undefined;
}): ReactElement {
  const collapsed = usePropertyPanelStore((state) => state.collapsed);
  const setCollapsed = usePropertyPanelStore((state) => state.setCollapsed);
  const activeTab = usePropertyPanelStore((state) => state.activeTab);
  const setActiveTab = usePropertyPanelStore((state) => state.setActiveTab);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const saveRunRef = useRef(0);
  const [draft, setDraft] = useState<RpaNodeConfigDraft>(() =>
    createNodeConfigDraft(selectedNode?.data ?? { description: '', kind: 'browser', status: 'pending', title: '' })
  );
  // 只挪动位置时 xyflow 会换掉节点对象但 data 不变，依赖挂整个 selectedNode 等于每次拖动都重建草稿、冲掉未保存的编辑
  const selectedNodeData = selectedNode?.data;
  const selectedNodeId = selectedNode?.id;
  const savedDraft = useMemo(() => (selectedNodeData === undefined ? null : createNodeConfigDraft(selectedNodeData)), [selectedNodeData]);
  const dirty = savedDraft !== null && JSON.stringify(draft) !== JSON.stringify(savedDraft);
  const [copied, setCopied] = useState(false);
  const setPendingDraft = usePropertyPanelStore((state) => state.setPendingDraft);

  // 运行走的是画布节点，面板里没保存的草稿它看不见——把草稿交给 store，运行前替运行方落盘。
  // 只有 dirty 时发布：否则每次选中节点都写一次 store，运行方要多判一次「和已存的一样吗」。
  useEffect(() => {
    if (selectedNodeId === undefined || !dirty) {
      setPendingDraft(null);
      return;
    }
    setPendingDraft({ nodeId: selectedNodeId, draft });
  }, [dirty, draft, selectedNodeId, setPendingDraft]);

  useEffect(() => () => setPendingDraft(null), [setPendingDraft]);

  // 放渲染期而非 effect：effect 会先用上个节点的草稿绘一帧，切节点时面板闪一下旧值
  const [syncedNode, setSyncedNode] = useState({ data: selectedNode?.data, id: selectedNode?.id });
  if (selectedNode?.id !== syncedNode.id || selectedNode?.data !== syncedNode.data) {
    setSyncedNode({ data: selectedNode?.data, id: selectedNode?.id });
    if (selectedNode !== undefined) {
      setDraft(createNodeConfigDraft(selectedNode.data));
    }
    if (selectedNode?.id !== syncedNode.id) {
      setSaveState('idle');
    }
  }

  const handleSave = (): void => {
    if (selectedNode === undefined) {
      return;
    }
    // runId 标记本次保存：延时回调期间用户若切换节点或再次保存，旧回调会因不匹配而放弃写状态
    const runId = saveRunRef.current + 1;
    const nodeId = selectedNode.id;
    const nextData = applyNodeConfigDraft(selectedNode.data, draft);
    saveRunRef.current = runId;
    setSaveState('saving');
    try {
      onUpdateNodeData(nodeId, nextData);
      window.setTimeout(() => {
        // 还要求当前仍是 saving：切换节点会把状态打回 idle，此时这次保存的提示已无归属，不应再冒出来
        setSaveState((current) => (current === 'saving' && saveRunRef.current === runId ? 'saved' : current));
        window.setTimeout(() => setSaveState((current) => (current === 'saved' ? 'idle' : current)), 1600);
      }, 180);
    } catch {
      if (saveRunRef.current === runId) {
        setSaveState('error');
      }
    }
  };

  const handleReset = (): void => {
    if (savedDraft !== null) {
      saveRunRef.current += 1;
      setDraft(savedDraft);
      setSaveState('idle');
    }
  };

  const handleCopy = (): void => {
    void navigator.clipboard?.writeText(JSON.stringify(draft, null, 2)).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <aside
      className={cn(
        'flex h-full min-h-0 shrink-0 flex-col overflow-hidden border-l border-slate-200 bg-white text-[11px]',
        'transition-[width] duration-200 ease-in-out',
        collapsed ? 'w-10' : 'w-72',
      )}
    >
      {/* Header — 折叠按钮放最左，保证 w-10 时仍可见 */}
      <div className="flex h-10 shrink-0 items-center gap-1.5 border-b border-slate-100 px-1.5">
        <IconButton
          label={collapsed ? '展开属性面板' : '折叠属性面板'}
          onClick={() => setCollapsed(!collapsed)}
        >
          {collapsed
            ? <PanelRightOpen className="h-3.5 w-3.5" strokeWidth={1.5} />
            : <PanelRightClose className="h-3.5 w-3.5" strokeWidth={1.5} />}
        </IconButton>
        <div
          className={cn(
            'flex min-w-0 flex-1 items-center gap-1.5',
            'transition-opacity duration-150',
            collapsed ? 'pointer-events-none opacity-0' : 'opacity-100',
          )}
        >
          <span className="text-[11px] font-bold text-slate-700">属性面板</span>
          {dirty && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" title="有未保存的修改" />}
        </div>
      </div>

      <div
        className={cn(
          'flex min-h-0 flex-1 flex-col overflow-hidden',
          'transition-opacity duration-150',
          collapsed ? 'pointer-events-none opacity-0' : 'opacity-100',
        )}
      >
        <Tabs className="min-h-0 flex flex-1 flex-col overflow-hidden" onValueChange={(value) => setActiveTab(value as PanelTab)} value={activeTab}>
          <TabsList className="grid h-7 shrink-0 grid-cols-3 border-b border-slate-100 text-[11px]">
            <TabsTrigger className="h-full text-[11px]" value="config">
              配置
            </TabsTrigger>
            <TabsTrigger className="h-full text-[11px]" value="io">
              输入输出
            </TabsTrigger>
            <TabsTrigger className="h-full text-[11px]" value="advanced">
              流程
            </TabsTrigger>
          </TabsList>
          <div className="min-h-0 flex-1 overflow-auto p-2.5 pb-14 text-[11px]">
            {selectedNode === undefined ? (
              activeTab === 'advanced' ? <AdvancedTab electron={electron} /> : <PropertyEmptyState />
            ) : (
              <PropertyContent activeTab={activeTab} draft={draft} electron={electron} flowEdges={flowEdges} flowNodes={flowNodes} inputVariables={inputVariables} node={selectedNode} onDraftChange={setDraft} />
            )}
          </div>
        </Tabs>
        <div className="flex h-11 shrink-0 items-center gap-1.5 border-t border-slate-200/60 bg-white/95 px-2.5 py-2 backdrop-blur-sm">
          <Button className="h-7 flex-1 gap-1.5" disabled={selectedNode === undefined || !dirty || saveState === 'saving'} onClick={handleSave} variant="primary">
            {getSaveButtonIcon(saveState)}
            {getSaveButtonText(dirty, saveState)}
          </Button>
          {dirty && (
            <Button aria-label="重置修改" className="h-7 w-7 shrink-0 px-0" onClick={handleReset} title="重置修改" variant="outline">
              <RotateCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
            </Button>
          )}
          <Button aria-label="复制配置 JSON" className="h-7 w-7 shrink-0 px-0 text-slate-400 hover:text-slate-600" disabled={selectedNode === undefined} onClick={handleCopy} title="复制配置 JSON" variant="ghost">
            {copied ? <Check className="h-3.5 w-3.5" strokeWidth={2} /> : <Copy className="h-3.5 w-3.5" strokeWidth={1.5} />}
          </Button>
        </div>
      </div>
    </aside>
  );
}

function getSaveButtonIcon(saveState: 'idle' | 'saving' | 'saved' | 'error'): ReactElement {
  if (saveState === 'saving') {
    return <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.5} />;
  }
  if (saveState === 'saved') {
    return <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={1.5} />;
  }
  if (saveState === 'error') {
    return <TriangleAlert className="h-3.5 w-3.5" strokeWidth={1.5} />;
  }
  return <Save className="h-3.5 w-3.5" strokeWidth={1.5} />;
}

function getSaveButtonText(dirty: boolean, saveState: 'idle' | 'saving' | 'saved' | 'error'): string {
  if (saveState === 'saving') {
    return '保存中';
  }
  if (saveState === 'saved') {
    return '已保存';
  }
  if (saveState === 'error') {
    return '保存失败';
  }
  return dirty ? '保存修改' : '暂无修改';
}
