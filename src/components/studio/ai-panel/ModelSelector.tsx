import { ChevronDown, WifiOff } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useRef, useState } from 'react';
import { useAiModelCatalogStore } from '../../../stores/useAiModelCatalogStore';
import type { AiModelMeta } from '../../../types/electron';
import { cn } from '../../../lib/utils';

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
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    // 每次展开都强制重拉：configured 随设置页里配的 API Key 变化
    const controller = new AbortController();
    void load({ force: true, signal: controller.signal });
    return () => controller.abort();
  }, [open, load]);

  useEffect(() => {
    const handler = (e: MouseEvent): void => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

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
    <div className="relative" ref={ref}>
      <button
        className={cn(
          'flex h-6 items-center gap-1 rounded-md text-[11px] transition-colors',
          variant === 'ghost'
            ? 'px-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-700'
            : 'border border-slate-200 bg-white px-2 text-slate-600 hover:border-slate-300 hover:bg-slate-50',
          open && variant === 'ghost' && 'bg-slate-100 text-slate-700',
          disabled && 'cursor-not-allowed opacity-50',
        )}
        disabled={disabled}
        onClick={() => {
          if (disabled) return;
          // 展开即重试：load 会先把 status 置为 loading，上一次的"后端不可用"提示自动清掉
          setOpen((o) => !o);
        }}
        title={disabled ? '生成中，下次发送时生效' : undefined}
        type="button"
      >
        <span className="max-w-30 truncate font-medium">{currentLabel}</span>
        <ChevronDown className={cn('h-3 w-3 shrink-0 text-slate-400 transition-transform', open && 'rotate-180')} />
      </button>

      {open && !disabled && (
        <div className={cn(
          'absolute left-0 z-(--z-dropdown) w-56 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg',
          placement === 'up' ? 'bottom-7' : 'top-7'
        )}>
          {backendDown && (
            <div className="flex items-center gap-1.5 border-b border-amber-100 bg-amber-50 px-2.5 py-1.5 text-[10px] text-amber-600">
              <WifiOff className="h-3 w-3 shrink-0" />
              后端离线，暂无可用模型
            </div>
          )}
          <div className="max-h-72 overflow-y-auto">
            {Object.entries(grouped).map(([provider, items]) => (
              <div key={provider}>
                <div className="sticky top-0 bg-slate-50 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                  {items[0]?.provider_label ?? provider}
                </div>
                {items.map((m) => {
                  const unavailable = !backendDown && m.configured === false && !m.local;
                  return (
                    <button
                      className={cn(
                        'flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[11px] hover:bg-slate-50',
                        m.id === value && 'bg-accent-soft text-accent-strong',
                        unavailable && 'opacity-40 cursor-not-allowed'
                      )}
                      disabled={unavailable}
                      key={m.id}
                      onClick={() => { onChange(m.id); setOpen(false); }}
                      type="button"
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
                      {m.legacy && (
                        <span className="shrink-0 text-[9px] text-slate-400">旧版</span>
                      )}
                      {unavailable && (
                        <span className="shrink-0 text-[9px] text-slate-500">未配置</span>
                      )}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
          <div className="border-t border-slate-100 px-2.5 py-1.5 text-[10px] text-slate-500">
            {backendDown ? '启动后端后配置状态将自动更新' : '在设置页配置 API Key'}
          </div>
        </div>
      )}
    </div>
  );
}
