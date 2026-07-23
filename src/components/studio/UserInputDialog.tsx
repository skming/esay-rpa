import { ExternalLink, MessageSquare } from 'lucide-react';
import type { ReactElement } from 'react';
import { useState } from 'react';

import { Button } from '../ui/button';
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';
import { Input } from '../ui/input';

export function UserInputDialog({
  prompt,
  url,
  onSubmit,
  onCancel
}: {
  prompt: string | null;
  url?: string | null;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}): ReactElement {
  const [value, setValue] = useState('');

  const handleSubmit = (): void => {
    onSubmit(value);
    setValue('');
  };

  return (
    <Dialog open={prompt !== null} onOpenChange={(open) => { if (!open && prompt !== null) onCancel(); }}>
      <DialogContent
        className="flex max-h-[calc(100vh-48px)] flex-col overflow-hidden sm:max-w-md"
        // 流程正暂停等待此输入，禁止 Esc/点击遮罩误关闭；只能走下方按钮显式停止/跳过/继续
        onEscapeKeyDown={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
        showClose={false}
      >
        <DialogHeader className="shrink-0">
          <DialogTitle className="flex items-center gap-2">
            <MessageSquare className="h-4 w-4 text-amber-500" />
            等待用户输入
          </DialogTitle>
        </DialogHeader>
        <DialogBody className="min-h-0 flex-1 overflow-y-auto">
          <div className="space-y-3 py-2">
            {prompt && (
              <p className="text-[12px] leading-relaxed text-slate-700">{prompt}</p>
            )}
            {url && (
              <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-slate-500">{url}</span>
                <button
                  className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-accent-strong transition-colors hover:bg-accent-soft"
                  onClick={() => { window.open(url, '_blank', 'noopener,noreferrer'); }}
                  type="button"
                >
                  <ExternalLink className="h-3 w-3" strokeWidth={1.8} />
                  打开网页
                </button>
              </div>
            )}
            <Input
              autoFocus
              placeholder="请输入..."
              value={value}
              onChange={(e) => { setValue(e.target.value); }}
              onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
            />
          </div>
        </DialogBody>
        <DialogFooter className="shrink-0">
          <Button variant="danger" onClick={onCancel}>
            停止流程
          </Button>
          {/* “跳过”约定为提交空字符串，后端据此判断本次输入被跳过而非填了空值 */}
          <Button variant="ghost" onClick={() => { onSubmit(''); setValue(''); }}>
            跳过
          </Button>
          <Button variant="primary" onClick={handleSubmit}>
            继续
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
