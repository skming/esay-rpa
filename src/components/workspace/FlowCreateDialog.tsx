import type { ReactElement } from 'react';
import { useEffect, useState } from 'react';

import { Button } from '../ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';
import { Input } from '../ui/input';

export function FlowCreateDialog({
  onCreate,
  onOpenChange,
  open
}: {
  onCreate: (name: string) => void;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}): ReactElement {
  const [name, setName] = useState('新建 RPA 流程');

  useEffect(() => {
    if (open) {
      setName('新建 RPA 流程');
    }
  }, [open]);

  const trimmedName = name.trim();

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新建流程</DialogTitle>
          <DialogDescription>创建草稿流程后进入画布编辑器，保存后会生成可调度版本。</DialogDescription>
        </DialogHeader>
        <DialogBody className="grid gap-1.5">
          <label className="text-[11px] font-medium text-slate-600" htmlFor="flow-name">
            流程名称
          </label>
          <Input
            id="flow-name"
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && trimmedName !== '') {
                onCreate(trimmedName);
              }
            }}
            value={name}
          />
        </DialogBody>
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)} variant="outline">
            取消
          </Button>
          <Button disabled={trimmedName === ''} onClick={() => onCreate(trimmedName)} variant="primary">
            创建并编辑
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
