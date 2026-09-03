import { GitBranch, LocateFixed, Pencil, Plus, Trash2 } from 'lucide-react';
import type { ReactElement } from 'react';
import { useState } from 'react';
import { Button } from '../../ui/button';
import type { FlowDiff, NodeLookupItem } from './aiPanelTypes';

function readNodeField(node: unknown, field: 'id' | 'title' | 'type'): string | undefined {
  if (node === null || typeof node !== 'object') return undefined;
  const value = (node as Record<string, unknown>)[field];
  return typeof value === 'string' && value ? value : undefined;
}

function formatNodeLabel(node: NodeLookupItem | undefined, fallbackId: string): string {
  if (!node) return fallbackId;
  if (node.title && node.type) return `${node.title} · ${node.id} · ${node.type}`;
  if (node.title) return `${node.title} · ${node.id}`;
  return node.id;
}

function LocateButton({ nodeId, onFocusNode }: { nodeId: string; onFocusNode?: (nodeId: string) => void }): ReactElement | null {
  if (!onFocusNode) return null;
  return (
    <Button
      className="ml-auto h-5 gap-1 rounded-md px-1.5 text-[10px]"
      onClick={() => onFocusNode(nodeId)}
      size="sm"
      title="定位到画布节点"
      variant="ghost"
    >
      <LocateFixed className="h-3 w-3" strokeWidth={1.75} />
      定位
    </Button>
  );
}

export function FlowDiffPreview({
  diff,
  onApply,
  onReject,
  nodeLookup,
  onFocusNode,
}: {
  diff: FlowDiff;
  onApply: () => Promise<void>;
  onReject: () => void;
  streamingPending?: boolean;
  nodeLookup?: Record<string, NodeLookupItem>;
  onFocusNode?: (nodeId: string) => void;
}): ReactElement {
  const [applying, setApplying] = useState(false);
  const [result, setResult] = useState<'ok' | 'error' | null>(null);
  const [errorMsg, setErrorMsg] = useState<string>('');

  const handleApply = async (): Promise<void> => {
    setApplying(true);
    try {
      await onApply();
      setResult('ok');
    } catch (e) {
      setErrorMsg((e as Error).message ?? '');
      setResult('error');
    } finally {
      setApplying(false);
    }
  };

  const addCount = (diff.add_nodes?.length ?? 0) + (diff.add_edges?.length ?? 0);
  const modCount = diff.update_nodes?.length ?? 0;
  const delCount = (diff.remove_node_ids?.length ?? 0) + (diff.remove_edge_ids?.length ?? 0);

  return (
    <div className="my-2 rounded-md border border-accent-line bg-accent-soft text-[11px]">
      <div className="flex items-center gap-1.5 border-b border-accent-line px-2.5 py-1.5">
        <GitBranch className="h-3 w-3 text-accent" />
        <span className="font-semibold text-accent-strong">流程变更预览</span>
        <span className="ml-auto text-accent-strong">
          {addCount > 0 && `+${addCount} `}
          {modCount > 0 && `~${modCount} `}
          {delCount > 0 && `-${delCount}`}
        </span>
      </div>

      <div className="max-h-40 overflow-auto px-2.5 py-1.5 space-y-0.5">
        {diff.add_nodes?.map((node, i) => {
          const id = readNodeField(node, 'id') ?? `new-${i}`;
          const item = { id, title: readNodeField(node, 'title'), type: readNodeField(node, 'type') };
          return (
            <div className="flex items-center gap-1.5 text-emerald-700" key={i}>
              <Plus className="h-3 w-3 shrink-0" />
              <span className="min-w-0 truncate">新增节点：{formatNodeLabel(item, id)}</span>
              <LocateButton nodeId={id} onFocusNode={onFocusNode} />
            </div>
          );
        })}
        {diff.update_nodes?.map((patch) => (
          <div className="text-amber-700" key={patch.id}>
            <div className="flex items-center gap-1.5">
              <Pencil className="h-3 w-3 shrink-0" />
              <span className="min-w-0 truncate">修改节点：{formatNodeLabel(nodeLookup?.[patch.id], patch.id)}</span>
              <LocateButton nodeId={patch.id} onFocusNode={onFocusNode} />
            </div>
            <pre className="ml-5 font-mono text-[10px] text-amber-800 leading-relaxed">
              {JSON.stringify(patch.patch, null, 2)}
            </pre>
          </div>
        ))}
        {diff.remove_node_ids?.map((id) => (
          <div className="flex items-center gap-1.5 text-red-600" key={id}>
            <Trash2 className="h-3 w-3 shrink-0" />
            <span className="min-w-0 truncate">删除节点：{formatNodeLabel(nodeLookup?.[id], id)}</span>
            <LocateButton nodeId={id} onFocusNode={onFocusNode} />
          </div>
        ))}
      </div>

      {result === 'ok' ? (
        <div className="border-t border-accent-line px-2.5 py-1.5 text-emerald-700">✓ 变更已应用</div>
      ) : result === 'error' ? (
        <div className="border-t border-accent-line px-2.5 py-1.5 text-red-600">
          ⚠ 应用失败{errorMsg ? `：${errorMsg}` : '，请重试'}
        </div>
      ) : (
        <div className="flex gap-2 border-t border-accent-line px-2.5 py-1.5">
          <Button disabled={applying} onClick={() => void handleApply()} size="sm" variant="primary">
            {applying ? '应用中…' : '应用变更'}
          </Button>
          <Button onClick={onReject} size="sm" variant="ghost">
            取消
          </Button>
        </div>
      )}
    </div>
  );
}
