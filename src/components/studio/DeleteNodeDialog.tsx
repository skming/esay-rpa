import { AlertTriangle, Trash2 } from 'lucide-react';
import type { ReactElement } from 'react';

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
            将删除「{target?.title ?? '当前步骤'}」并自动重连前后节点。此操作会修改当前画布，保存前可通过重新打开版本快照恢复。
          </AlertDialogDescription>
        </AlertDialogHeader>
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
