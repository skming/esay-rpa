import { BrushCleaning } from 'lucide-react';
import { type ReactElement, useState } from 'react';

import { IconButton } from '../../ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle
} from '../../ui/alert-dialog';

/**
 * 清空不可逆，按钮又紧挨着「浮窗 / 关闭」这两个无害操作：
 * 加二次确认，并在无对话或生成中时禁用。
 */
export function ClearChatButton({
  messageCount,
  onClear,
  pending
}: {
  messageCount: number;
  onClear: () => void;
  pending: boolean;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const disabled = messageCount === 0 || pending;

  return (
    <>
      <IconButton
        className="text-slate-500 hover:bg-red-50 hover:text-red-600"
        disabled={disabled}
        label={pending ? '生成中，无法清空对话' : '清空对话'}
        onClick={() => setOpen(true)}
      >
        <BrushCleaning className="h-3.5 w-3.5" />
      </IconButton>
      <AlertDialog onOpenChange={setOpen} open={open}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>清空当前对话？</AlertDialogTitle>
            <AlertDialogDescription>
              将删除这轮会话的 {messageCount} 条消息，包括助手给出的流程改动建议。此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={onClear}>清空</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
