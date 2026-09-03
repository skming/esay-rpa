import { Bug, Circle, Copy, Pencil, Play, Plus, Trash2 } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ReactElement, ReactNode } from 'react';

import { cn } from '../../lib/utils';
import type { ContextMenuAction } from '../../types/rpa';
import { ContextMenu as UiContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuSeparator, ContextMenuTrigger } from '../ui/context-menu';

export function ContextMenu({
  children,
  hasBreakpoint = false,
  nodeId,
  nodeTitle,
  onAction,
  onOpenChange
}: {
  children: ReactNode;
  hasBreakpoint?: boolean;
  nodeId: string;
  nodeTitle: string;
  onAction: (action: ContextMenuAction, nodeId: string) => void;
  onOpenChange?: (open: boolean) => void;
}): ReactElement {
  const actions = [
    { action: 'edit', label: '编辑步骤', color: 'text-accent-strong', icon: Pencil },
    { action: 'run-from-here', label: '从此处运行', color: 'text-emerald-700', icon: Play },
    { action: 'breakpoint', label: hasBreakpoint ? '移除断点' : '添加断点', color: 'text-amber-800', icon: Bug },
    { action: 'duplicate', label: '复制步骤', color: 'text-slate-600', icon: Copy },
    { action: 'insert-before', label: '前插步骤', color: 'text-slate-600', icon: Plus },
    { action: 'insert-after', label: '后插步骤', color: 'text-slate-600', icon: Plus },
    { label: 'separator' },
    { action: 'disable', label: '禁用步骤', color: 'text-slate-500', icon: Circle },
    { action: 'delete', label: '删除步骤', color: 'text-red-600', icon: Trash2 }
  ] satisfies Array<{ action?: ContextMenuAction; label: string; color?: string; icon?: LucideIcon }>;

  return (
    <UiContextMenu onOpenChange={onOpenChange}>
      <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
      <ContextMenuContent className="w-40">
        <div className="border-b border-slate-100 px-2 pb-1.5 text-[10px] text-slate-500">{nodeTitle}</div>
        {actions.map((action, index) => {
          if (action.label === 'separator') {
            return <ContextMenuSeparator key={`${action.label}-${index}`} />;
          }

          const Icon = action.icon ?? Circle;
          return (
            <ContextMenuItem
              className={cn('gap-2', action.color)}
              key={action.label}
              onSelect={() => {
                if (action.action !== undefined) {
                  onAction(action.action, nodeId);
                }
              }}
            >
              <Icon className="h-3.5 w-3.5" strokeWidth={1.5} />
              {action.label}
            </ContextMenuItem>
          );
        })}
      </ContextMenuContent>
    </UiContextMenu>
  );
}
