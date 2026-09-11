import type { ReactElement } from 'react';
import { useRef, useState } from 'react';

import { Button } from '../ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';
import { Input } from '../ui/input';

const DEFAULT_FLOW_NAME = '新建 RPA 流程';

export function FlowCreateDialog({
  onCreate,
  onOpenChange,
  open
}: {
  onCreate: (name: string) => Promise<void>;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}): ReactElement {
  const creatingRef = useRef(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState(DEFAULT_FLOW_NAME);

  // 每次打开都回到默认名。渲染期按前值调整而非 effect：后者会先绘出上次输入的残留名字
  const [prevOpen, setPrevOpen] = useState(open);
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) {
      setName(DEFAULT_FLOW_NAME);
    }
  }

  const trimmedName = name.trim();

  const create = async () => {
    if (!trimmedName || creatingRef.current) return;
    creatingRef.current = true;
    setCreating(true);
    try {
      await onCreate(trimmedName);
    } finally {
      creatingRef.current = false;
      setCreating(false);
    }
  };

  return (
    <Dialog onOpenChange={(next) => { if (!creatingRef.current) onOpenChange(next); }} open={open}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新建流程</DialogTitle>
          <DialogDescription>草稿会立即保存，退出后可从流程列表继续编辑。</DialogDescription>
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
                void create();
              }
            }}
            value={name}
          />
        </DialogBody>
        <DialogFooter>
          <Button disabled={creating} onClick={() => onOpenChange(false)} variant="outline">
            取消
          </Button>
          <Button disabled={creating || trimmedName === ''} onClick={() => { void create(); }} variant="primary">
            {creating ? '正在创建…' : '创建并编辑'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
