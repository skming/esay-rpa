import { AlertTriangle, Trash2 } from 'lucide-react';
import type { ReactElement } from 'react';

import type { DeleteImpact } from '../../lib/flowOperations';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle
} from '../ui/alert-dialog';

export type DeleteNodeTarget = {
  id: string;
  title: string;
  impact: DeleteImpact;
} | null;

export function DeleteNodeDialog({
  onConfirm,
  onOpenChange,
  target
}: {
  onConfirm: () => void;
  onOpenChange: (open: boolean) => void;
  target: DeleteNodeTarget;
}): ReactElement {
  // 删除只重连「第一条入边 → 第一条出边」，循环体回边、条件分支这些多出来的连线是直接丢掉的。
  // 不说出来的话，用户以为自己删了一个步骤，实际是把一整条分支从流程里摘了——画布上看不出来，
  // 要等到下次运行少跑一半才发现。
  const impact = target?.impact;
  const reconnects = impact?.reconnects ?? true;
  const droppedEdgeCount = impact?.droppedEdgeCount ?? 0;
  const droppedNeighborTitles = impact?.droppedNeighborTitles ?? [];

  return (
    <AlertDialog onOpenChange={onOpenChange} open={target !== null}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <div className="flex items-center gap-2">
            <span className="grid h-8 w-8 place-items-center rounded-lg border border-red-100 bg-red-50 text-red-600">
              <AlertTriangle className="h-4 w-4" strokeWidth={1.5} />
            </span>
            <AlertDialogTitle>删除步骤</AlertDialogTitle>
          </div>
          <AlertDialogDescription>
            将删除「{target?.title ?? '当前步骤'}」{reconnects ? '并自动重连前后节点' : ''}。此操作会修改当前画布，保存前可通过重新打开版本快照恢复。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {(!reconnects || droppedEdgeCount > 0) && (
          <div className="flex flex-col gap-1 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-[11px] leading-relaxed text-amber-700">
            {!reconnects && <p>该步骤没有可直接对接的前后节点，删除后这一段流程会断开，需要你手动连回。</p>}
            {droppedEdgeCount > 0 && (
              <p>
                另有 {droppedEdgeCount} 条连线会被一并删除且不会重连
                {droppedNeighborTitles.length > 0 && `（涉及 ${droppedNeighborTitles.join('、')}）`}
                ，相关分支需要你手动接回。
              </p>
            )}
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>
            <Trash2 className="h-3.5 w-3.5" strokeWidth={1.5} />
            确认删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
