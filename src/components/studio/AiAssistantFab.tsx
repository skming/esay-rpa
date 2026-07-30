import { BotMessageSquare } from 'lucide-react';
import type { ReactElement } from 'react';

import { cn } from '../../lib/utils';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';

export function AiAssistantFab({
  busy,
  hidden,
  onClick,
}: {
  busy: boolean;
  hidden: boolean;
  onClick: () => void;
}): ReactElement {
  const label = busy ? 'RPA 助手处理中' : 'RPA 助手';
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          // 面板开着时按钮虽然看不见，但仍在 Tab 焦点序列里，会让键盘用户按到一个"隐形按钮"
          aria-hidden={hidden}
          aria-label={label}
          className={cn(
            // fixed 而非 absolute：位置只认窗口右下角，不受画布/属性面板的收合与滚动影响
            'fixed bottom-5 right-5 z-(--z-dropdown) flex h-11 w-11 items-center justify-center rounded-full',
            'bg-brand-gradient text-(--color-accent-fg) shadow-[0_8px_32px_rgba(15,23,42,0.10),0_2px_8px_rgba(15,23,42,0.06)]',
            'transition-all duration-200 hover:opacity-90 active:scale-[0.97]',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-soft focus-visible:ring-offset-2',
            // 运行信号统一用 live 蓝：静态描边保证降级可读（含 prefers-reduced-motion），呼吸环负责"活着"
            busy && 'fab-live-ring ring-2 ring-live ring-offset-2 ring-offset-slate-50',
            // 面板打开时用 pointer-events-none 而非卸载，让淡出动画能跑完
            hidden && 'pointer-events-none scale-90 opacity-0',
          )}
          onClick={onClick}
          tabIndex={hidden ? -1 : 0}
          type="button"
        >
          <BotMessageSquare className="h-5 w-5" strokeWidth={1.6} />
        </button>
      </TooltipTrigger>
      <TooltipContent side="left">
        {label}
        <span className="ml-1.5 text-slate-400">⌘J</span>
      </TooltipContent>
    </Tooltip>
  );
}
