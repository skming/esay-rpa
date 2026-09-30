import type { ReactNode } from 'react';
import type { ConnectionPhase, ConnectionStatus } from '../lib/connection';

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
  return { title: '正在连接客户端', description: '请确认 Easy RPA 客户端已启动，然后打开此弹窗重试。', tone: 'waiting' };
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2.5 text-[11px]">
      <span className="w-16 shrink-0 text-ink-4">{label}</span>
      <span className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-ink-2">{children}</span>
    </div>
  );
}

export function BridgeStatusCard({ status, phase }: { status: ConnectionStatus | null; phase: ConnectionPhase }) {
  const connected = status?.connected ?? false;
  const content = statusContent(phase, connected);
  const tone = toneClasses[content.tone];

  return (
    <section aria-live="polite" className={`rounded-lg border p-3.5 ${tone.card}`}>
      <div className="flex items-center gap-2.5">
        <span className={`h-2 w-2 shrink-0 rounded-full ${tone.dot}`} />
        <span className="text-[13px] font-semibold text-ink-2">{content.title}</span>
        {connected && status && (
          <span className="ml-auto rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] text-emerald-700">
            心跳 {status.heartbeatSeconds}s
          </span>
        )}
      </div>
      <p className="mt-1.5 pl-4.5 text-[11px] leading-5 text-ink-3">{content.description}</p>
      {status && (
        // 桥的常驻连接是到本地后端的 WebSocket；CDP 只在可信输入时临时挂载，不作常驻会话，故分行如实标注。
        <div className="mt-3 flex flex-col gap-2 border-t border-dashed border-rule-2 pt-3">
          <DetailRow label="桥接协议">WebSocket</DetailRow>
          <DetailRow label="连接目标">{status.bridgeUrl}</DetailRow>
          {connected ? (
            <DetailRow label="可信输入">chrome.debugger · 按需注入</DetailRow>
          ) : (
            <DetailRow label="重连">后端就绪后自动接入</DetailRow>
          )}
        </div>
      )}
    </section>
  );
}
