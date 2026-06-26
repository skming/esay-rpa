import { MessageSquare } from 'lucide-react';
import type { ReactElement } from 'react';
import { useState } from 'react';

import { Button } from '../ui/button';
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';

export function UserInputDialog({
  prompt,
  onSubmit,
  onCancel
}: {
  prompt: string | null;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}): ReactElement {
  const [value, setValue] = useState('');

  const handleSubmit = (): void => {
    onSubmit(value);
    setValue('');
  };

  return (
    <Dialog open={prompt !== null} onOpenChange={(open) => { if (!open && prompt === null) onCancel(); }}>
      <DialogContent
        className="flex max-h-[calc(100vh-48px)] flex-col overflow-hidden sm:max-w-md"
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
            <Label className="text-sm text-slate-600">{prompt}</Label>
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
          <Button variant="outline" onClick={onCancel}>
            停止流程
          </Button>
          <Button variant="ghost" onClick={() => { onSubmit(''); setValue(''); }}>
            跳过
          </Button>
          <Button onClick={handleSubmit}>
            确认提交
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
