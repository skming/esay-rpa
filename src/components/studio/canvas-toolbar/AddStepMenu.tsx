import { ChevronDown, Plus } from 'lucide-react';
import type { ReactElement } from 'react';

import { kindStyles } from '../../../data/studioData';
import type { NodeKind } from '../../../types/rpa';
import { Button } from '../../ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../../ui/dropdown-menu';

export function AddStepMenu({ onQuickAdd }: { onQuickAdd: (kind: NodeKind) => void }): ReactElement {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button className="hidden 2xl:inline-flex" variant="soft">
          <Plus className="h-3.5 w-3.5" strokeWidth={1.5} />
          添加步骤
          <ChevronDown className="h-3 w-3" strokeWidth={1.5} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-40">
        {(['browser', 'excel', 'control'] satisfies NodeKind[]).map((kind) => {
          const style = kindStyles[kind];
          const Icon = style.icon;
          return (
            <DropdownMenuItem className="gap-2" key={kind} onClick={() => onQuickAdd(kind)}>
              <Icon className="h-3.5 w-3.5" style={{ color: style.accent }} strokeWidth={1.5} />
              <span>{kind === 'browser' ? '浏览器步骤' : kind === 'excel' ? 'Excel 操作' : '流程控制'}</span>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
