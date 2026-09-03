import { ChevronDown, WifiOff } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useState } from 'react';

import { cn } from '../../../lib/utils';
import { useAiModelCatalogStore } from '../../../stores/useAiModelCatalogStore';
import type { AiModelMeta } from '../../../types/electron';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '../../ui/dropdown-menu';

export function ModelSelector({
  value,
  onChange,
  placement = 'down',
  variant = 'bordered',
  disabled = false,
}: {
  value: string;
  onChange: (model: string) => void;
  placement?: 'down' | 'up';
  /** 'bordered' for the panel header, 'ghost' for the composer toolbar. */
  variant?: 'bordered' | 'ghost';
  /** When true the trigger is non-interactive (e.g. while AI is streaming). */
  disabled?: boolean;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const models = useAiModelCatalogStore((s) => s.models);
  const status = useAiModelCatalogStore((s) => s.status);
  const load = useAiModelCatalogStore((s) => s.load);
  const backendDown = status === 'error';

  useEffect(() => {
    if (!open) return;
    // 每次展开都强制重拉：configured 随设置页里配的 API Key 变化
    const controller = new AbortController();
    void load({ force: true, signal: controller.signal });
    return () => controller.abort();
  }, [open, load]);

  const displayModels = backendDown ? [] : models;

  const current = models.find((m) => m.id === value);
  const currentLabel = current?.label ?? value;

  const grouped = displayModels.reduce<Record<string, AiModelMeta[]>>((acc, m) => {
    const group = m.provider ?? 'other';
    (acc[group] ??= []).push(m);
    return acc;
  }, {});
  // 被新版取代的型号沉到各分组末尾，当前主力留在视线内
  for (const items of Object.values(grouped)) {
    items.sort((a, b) => Number(a.legacy ?? false) - Number(b.legacy ?? false));
  }

  return (
    <DropdownMenu onOpenChange={setOpen} open={open}>
      {/* 换到 Radix 之前这里是一个 div 套 button 的手搓菜单：没有 aria-expanded /
          aria-haspopup，选项是一排普通 button（读屏听不出「这是一组单选」，也听不出
          哪个正在生效），Esc 关不掉、方向键不走、关闭后焦点不回来。这些都由 Radix
          的 menu + menuitemradio 语义与焦点管理提供，不需要在这里重写一遍。 */}
      <DropdownMenuTrigger asChild disabled={disabled}>
        <button
          aria-label={`AI 模型：${currentLabel}`}
          className={cn(
            'flex h-6 items-center gap-1 rounded-md text-[11px] transition-colors',
            variant === 'ghost'
              ? 'px-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-700'
              : 'border border-slate-200 bg-white px-2 text-slate-600 hover:border-slate-300 hover:bg-slate-50',
            open && variant === 'ghost' && 'bg-slate-100 text-slate-700',
            disabled && 'cursor-not-allowed opacity-50',
          )}
          title={disabled ? '生成中，下次发送时生效' : undefined}
          type="button"
        >
          <span className="max-w-30 truncate font-medium">{currentLabel}</span>
          <ChevronDown className={cn('h-3 w-3 shrink-0 text-slate-500 transition-transform', open && 'rotate-180')} />
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent
        align="start"
        className="w-56 p-0"
        side={placement === 'up' ? 'top' : 'bottom'}
        sideOffset={4}
      >
        {backendDown && (
          <div className="flex items-center gap-1.5 border-b border-amber-100 bg-amber-50 px-2.5 py-1.5 text-[10px] text-amber-800">
            <WifiOff className="h-3 w-3 shrink-0" />
            后端离线，暂无可用模型
          </div>
        )}
        <div className="max-h-72 overflow-y-auto p-1.5">
          <DropdownMenuRadioGroup onValueChange={onChange} value={value}>
            {Object.entries(grouped).map(([provider, items]) => {
              const groupLabel = items[0]?.provider_label ?? provider;
              return (
                // 分组名同时给读屏（aria-label）和眼睛（下面那行）用；可见的那行标记
                // aria-hidden，否则同一个名字会被念两遍
                <DropdownMenuGroup aria-label={groupLabel} key={provider}>
                  <div
                    aria-hidden="true"
                    className="sticky top-0 -mx-1.5 bg-slate-50 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500"
                  >
                    {groupLabel}
                  </div>
                  {items.map((m) => {
                    const unavailable = !backendDown && m.configured === false && !m.local;
                    return (
                      <DropdownMenuRadioItem
                        className="h-7 gap-2 rounded-md pl-7 pr-2 text-[11px] data-[state=checked]:bg-accent-soft data-[state=checked]:text-accent-strong"
                        disabled={unavailable}
                        key={m.id}
                        value={m.id}
                      >
                        <span className={cn('flex-1 truncate', m.legacy && 'text-slate-500')}>{m.label}</span>
                        {m.recommended && (
                          <span className="shrink-0 rounded bg-accent-soft px-1 py-0.5 text-[9px] font-medium text-accent-strong">
                            推荐
                          </span>
                        )}
                        {m.badge && (
                          <span className="shrink-0 rounded bg-accent-soft px-1 py-0.5 text-[9px] font-medium text-accent-strong">
                            {m.badge}
                          </span>
                        )}
                        {m.legacy && <span className="shrink-0 text-[9px] text-slate-500">旧版</span>}
                        {unavailable && <span className="shrink-0 text-[9px] text-slate-500">未配置</span>}
                      </DropdownMenuRadioItem>
                    );
                  })}
                </DropdownMenuGroup>
              );
            })}
          </DropdownMenuRadioGroup>
        </div>
        <div className="border-t border-slate-100 px-2.5 py-1.5 text-[10px] text-slate-500">
          {backendDown ? '启动后端后配置状态将自动更新' : '在设置页配置 API Key'}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
