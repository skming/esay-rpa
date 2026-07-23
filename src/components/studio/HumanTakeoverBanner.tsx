import { ExternalLink, UserCheck } from 'lucide-react';
import { useEffect, useState, type ReactElement } from 'react';

import { cn } from '../../lib/utils';

type HumanTakeoverBannerProps = {
  message: string | null;
  url?: string | null;
  onOpenPage?: (url: string) => void;
  onResume: (mode: 'next_node' | 'current_node') => void;
  onStop: () => void;
};

type Parsed = { title: string; body: string; url: string | null; timeoutMs: number };

const TIMER_RE = /⏱️?(\d+)/g;

// 消息格式："{nodeTitle}\n{body}\n⏱{timeoutMs}"
function parseMessage(raw: string, urlProp?: string | null): Parsed {
  let timeoutMs = 300_000;
  const withoutTimers = raw
    .replace(TIMER_RE, (_, ms) => { timeoutMs = parseInt(ms, 10); return ''; })
    .replace(/\n{2,}/g, '\n')
    .trim();

  const urlMatch = withoutTimers.match(/\n(https?:\/\/\S+)$/);
  const withoutUrl = urlMatch ? withoutTimers.slice(0, urlMatch.index!).trim() : withoutTimers;
  const url = urlProp ?? (urlMatch ? urlMatch[1] : null);

  const nl = withoutUrl.indexOf('\n');
  const title = nl >= 0 ? withoutUrl.slice(0, nl).trim() : (withoutUrl || '需要您的操作');
  const body = nl >= 0 ? withoutUrl.slice(nl + 1).trim() : '';

  return { title, body, url, timeoutMs };
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function useCountdown(totalSeconds: number, active: boolean): number {
  const [remaining, setRemaining] = useState(totalSeconds);

  // 放渲染期而非 effect：effect 会先绘一帧上轮残余秒数，横幅弹出瞬间闪一个错的倒计时
  const [round, setRound] = useState({ active, totalSeconds });
  if (active !== round.active || totalSeconds !== round.totalSeconds) {
    setRound({ active, totalSeconds });
    setRemaining(totalSeconds);
  }

  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => {
      setRemaining((prev) => (prev <= 1 ? 0 : prev - 1));
    }, 1000);
    return () => { clearInterval(id); };
  }, [active, totalSeconds]);

  return remaining;
}

export function HumanTakeoverBanner({ message, url, onOpenPage, onResume, onStop }: HumanTakeoverBannerProps): ReactElement | null {
  const active = message !== null;
  const { title, body, url: parsedUrl, timeoutMs } = active
    ? parseMessage(message, url)
    : { title: '', body: '', url: null, timeoutMs: 300_000 };

  const displayUrl = parsedUrl?.startsWith('http') ? parsedUrl : null;
  const totalSeconds = Math.round(timeoutMs / 1000);
  const remaining = useCountdown(totalSeconds, active);
  const timedOut = remaining === 0;
  const urgent = remaining <= 60 && remaining > 0;
  const progressPct = timedOut ? 0 : (remaining / totalSeconds) * 100;

  if (!active) return null;

  return (
    <div className="animate-in fade-in-0 slide-in-from-top-3 fixed left-1/2 top-4 z-(--z-banner) w-130 -translate-x-1/2 overflow-hidden rounded-xl bg-white shadow-[0_12px_40px_rgba(15,23,42,0.14),0_2px_8px_rgba(15,23,42,0.06)] duration-200">
      {/* 进度条在 timeoutMs 内线性归零 */}
      <div className="h-0.75 w-full bg-amber-100">
        <div
          className={cn(
            'h-full motion-safe:transition-[width] motion-safe:duration-1000 motion-safe:ease-linear',
            timedOut ? 'bg-red-400' : urgent ? 'bg-orange-400' : 'bg-amber-400'
          )}
          style={{ width: `${progressPct}%` }}
        />
      </div>

      <div className="flex items-start gap-3 px-5 pb-3 pt-4">
        <div className={cn(
          'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg',
          timedOut ? 'bg-red-50' : urgent ? 'bg-orange-50' : 'bg-amber-50'
        )}>
          <UserCheck
            className={timedOut ? 'text-red-400' : urgent ? 'text-orange-500' : 'text-amber-500'}
            size={16}
            strokeWidth={2}
          />
        </div>
        <div className="min-w-0 flex-1 pt-0.5">
          <div className="flex items-baseline justify-between gap-2">
            <p className="text-[13px] font-semibold leading-tight text-slate-900">{title}</p>
            <span
              className={cn(
                'shrink-0 font-mono text-[11px] tabular-nums',
                timedOut ? 'text-red-500' : urgent ? 'text-orange-500' : 'text-slate-500'
              )}
            >
              {timedOut ? '已超时' : formatTime(remaining)}
            </span>
          </div>
          {body.length > 0 && (
            <p className="mt-1 text-[12px] leading-relaxed text-slate-500">{body}</p>
          )}
        </div>
      </div>

      {displayUrl && (
        <div className="px-5 pb-3">
          <div className="flex items-center gap-1 rounded-lg bg-slate-50 px-3 py-2 ring-1 ring-slate-200/80">
            <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-slate-500">{displayUrl}</span>
            {onOpenPage && (
              <button
                type="button"
                className="ml-1 shrink-0 rounded-md p-1 text-slate-500 transition-colors hover:bg-slate-200 hover:text-slate-600"
                onClick={() => { onOpenPage(displayUrl); }}
                title="在浏览器中打开"
              >
                <ExternalLink className="h-3.5 w-3.5" strokeWidth={1.8} />
              </button>
            )}
          </div>
        </div>
      )}

      <div className="mx-5 border-t border-slate-100" />

      <div className="flex items-center gap-2 px-5 py-3.5">
        <button
          type="button"
          disabled={timedOut}
          className={cn(
            'h-8 flex-1 rounded-lg text-[12.5px] font-medium text-white shadow-sm transition-colors active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50',
            urgent ? 'bg-orange-500 hover:bg-orange-600' : 'bg-amber-500 hover:bg-amber-600'
          )}
          onClick={() => { onResume('next_node'); }}
        >
          已完成，继续
        </button>
        <button
          type="button"
          className="h-8 rounded-lg border border-slate-200 bg-white px-4 text-[12px] font-medium text-slate-700 transition-all hover:bg-slate-50 active:scale-[0.98]"
          onClick={() => { onResume('current_node'); }}
        >
          重试
        </button>
        <button
          type="button"
          className="h-8 rounded-lg px-4 text-[12px] font-medium text-slate-500 transition-all hover:bg-slate-100 hover:text-slate-600 active:scale-[0.98]"
          onClick={onStop}
        >
          停止
        </button>
      </div>
    </div>
  );
}
