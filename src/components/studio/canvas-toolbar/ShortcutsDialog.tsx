import { Keyboard } from 'lucide-react';
import { type ReactElement, useState } from 'react';

import { IconButton } from '../../ui/button';
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle } from '../../ui/dialog';

// ⌘ 在非 macOS 上按 Ctrl 显示，其余键位两端一致
const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
const CMD = isMac ? '⌘' : 'Ctrl';

const SHORTCUT_GROUPS: Array<{ items: Array<{ keys: string[]; label: string }>; title: string }> = [
  {
    title: '画布视图',
    items: [
      { keys: ['V'], label: '选择工具' },
      { keys: ['H'], label: '平移工具' },
      { keys: ['空格', '拖拽'], label: '临时平移（松开还原工具）' },
      { keys: ['中键', '拖拽'], label: '任意工具下平移画布' },
      { keys: ['F'], label: '适应视图' },
      { keys: ['G'], label: '显示/隐藏网格' },
      { keys: ['M'], label: '显示/隐藏缩略图' },
      { keys: [CMD, '+'], label: '放大' },
      { keys: [CMD, '−'], label: '缩小' },
      { keys: [CMD, '0'], label: '缩放回 100%' },
      { keys: [CMD, '\\'], label: '专注模式（收起全部面板）' }
    ]
  },
  {
    title: '节点与流程',
    items: [
      { keys: ['Enter'], label: '编辑选中节点' },
      { keys: [CMD, 'D'], label: '复制选中节点' },
      { keys: ['B'], label: '切换断点' },
      { keys: ['Delete'], label: '删除选中节点 / 连线' },
      { keys: [CMD, 'Z'], label: '撤销（含节点拖动）' },
      { keys: [CMD, '⇧', 'Z'], label: '重做' },
      { keys: [CMD, 'S'], label: '保存流程' },
      { keys: ['Esc'], label: '取消选择 / 退出输入框' }
    ]
  }
];

export function ShortcutsDialog(): ReactElement {
  const [open, setOpen] = useState(false);

  return (
    <>
      <IconButton label="快捷键" onClick={() => setOpen(true)}>
        <Keyboard className="h-3.5 w-3.5" strokeWidth={1.5} />
      </IconButton>
      <Dialog onOpenChange={setOpen} open={open}>
        <DialogContent className="flex max-h-[calc(100vh-48px)] max-w-lg flex-col overflow-hidden">
          <DialogHeader className="shrink-0">
            <DialogTitle>键盘快捷键</DialogTitle>
          </DialogHeader>
          <DialogBody className="min-h-0 flex-1 space-y-4 overflow-y-auto">
            {SHORTCUT_GROUPS.map((group) => (
              <div key={group.title}>
                <div className="mb-1.5 text-[11px] font-medium text-slate-500">{group.title}</div>
                <div className="space-y-0.5">
                  {group.items.map((item) => (
                    <div className="flex items-center justify-between gap-4 rounded px-1.5 py-1 text-[12px] text-slate-700 hover:bg-slate-50" key={item.label}>
                      <span className="min-w-0 truncate">{item.label}</span>
                      <span className="flex shrink-0 items-center gap-1">
                        {item.keys.map((key) => (
                          <kbd className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 font-mono text-[10px] text-slate-600" key={key}>
                            {key}
                          </kbd>
                        ))}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </DialogBody>
        </DialogContent>
      </Dialog>
    </>
  );
}
