import type { ConnectionPhase } from '../lib/connection';

type StatusTone = 'success' | 'waiting' | 'neutral' | 'error';

const toneClasses: Record<StatusTone, { card: string; dot: string }> = {
  success: { card: 'border-emerald-200 bg-emerald-50/70', dot: 'bg-emerald-500' },
  waiting: { card: 'border-amber-200 bg-amber-50/70', dot: 'bg-amber-500' },
  neutral: { card: 'border-rule-2 bg-paper-sunk/70', dot: 'bg-ink-4' },
  error: { card: 'border-red-200 bg-red-50/70', dot: 'bg-red-500' },
};

function statusContent(phase: ConnectionPhase, connected: boolean): { description: string; title: string; tone: StatusTone } {
  if (phase === 'checking') {
    return { title: '正在检查连接', description: '正在唤醒浏览器扩展…', tone: 'neutral' };
  }
  if (phase === 'error') {
    return { title: '扩展后台未响应', description: '请在扩展管理页重新加载 Easy RPA。', tone: 'error' };
  }
  if (connected) {
    return { title: '已连接', description: '可以使用当前 Chrome 运行流程。', tone: 'success' };
  }
  return { title: '正在连接客户端', description: '打开 Easy RPA 后会自动恢复连接。', tone: 'waiting' };
}

export function BridgeStatusCard({ connected, phase }: { connected: boolean; phase: ConnectionPhase }) {
  const content = statusContent(phase, connected);
  const tone = toneClasses[content.tone];

  return (
    <section aria-live="polite" className={`rounded-lg border p-3.5 ${tone.card}`}>
      <div className="flex items-center gap-2.5">
        <span className={`h-2 w-2 shrink-0 rounded-full ${tone.dot}`} />
        <span className="text-[13px] font-semibold text-ink-2">{content.title}</span>
      </div>
      <p className="mt-1.5 pl-4.5 text-[11px] leading-5 text-ink-3">{content.description}</p>
    </section>
  );
}
