import type { ReactNode } from 'react';
import type { ConnectionStatus, ManagedGroup, ManagedTab } from '../lib/connection';

// chrome.tabGroups 的固定色名 → 展示色块，与浏览器分组颜色对齐（读不到的色名回落灰）。
const GROUP_COLOR_HEX: Record<string, string> = {
  grey: '#94a3b8',
  blue: '#3b82f6',
  red: '#ef4444',
  yellow: '#f59e0b',
  green: '#10b981',
  pink: '#ec4899',
  purple: '#8b5cf6',
  cyan: '#06b6d4',
  orange: '#f97316',
};

function hostOf(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.host}${parsed.pathname === '/' ? '' : parsed.pathname}`;
  } catch {
    return url;
  }
}

// 内联 SVG 而非远程 favicon：popup 不引第三方请求，也不暴露正在托管的站点给外部。
function GlobeIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className="h-3.5 w-3.5" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18" />
    </svg>
  );
}

function Chip({ children }: { children: ReactNode }) {
  return (
    <span className="flex items-center gap-1.5 rounded-full border border-rule bg-paper-sunk px-2 py-0.5 text-[10px] text-ink-3">
      {children}
    </span>
  );
}

function TabRow({ tab }: { tab: ManagedTab | null }) {
  if (!tab) {
    return <div className="rounded-lg bg-paper-sunk px-2.5 py-2 text-[11px] text-ink-4">空闲 · 暂无受控标签页</div>;
  }
  return (
    <div className="flex items-center gap-2.5 rounded-lg bg-paper-sunk px-2.5 py-2">
      <span className="flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded border border-rule bg-surface text-ink-4">
        <GlobeIcon />
      </span>
      <div className="min-w-0">
        <div className="truncate text-[11.5px] font-medium text-ink-2">{tab.title}</div>
        {tab.url && <div className="truncate font-mono text-[10px] text-ink-4">{hostOf(tab.url)}</div>}
      </div>
    </div>
  );
}

function GroupChip({ group }: { group: ManagedGroup | null }) {
  if (!group) {
    return (
      <Chip>
        <span className="h-1.5 w-1.5 rounded-full bg-ink-4" />
        未分组
      </Chip>
    );
  }
  return (
    <Chip>
      <span className="h-1.5 w-1.5 rounded-sm" style={{ backgroundColor: GROUP_COLOR_HEX[group.color] ?? GROUP_COLOR_HEX.grey }} />
      {group.title}
    </Chip>
  );
}

export function ManagedSessionCard({ status }: { status: ConnectionStatus }) {
  return (
    <section className="border-t border-rule pt-3">
      <h2 className="mb-2 text-[10px] text-ink-4">托管信息</h2>
      <div className="space-y-2">
        <TabRow tab={status.controlledTab} />
        {status.explorationTab && (
          <div className="flex items-center gap-1.5 rounded-md border border-violet-500/25 bg-violet-500/10 px-2 py-1.5 text-[10px] text-violet-700">
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-violet-500" />
            <span className="shrink-0">AI 正在探索页面</span>
            <span className="min-w-0 truncate font-mono text-violet-600/80">{hostOf(status.explorationTab.url)}</span>
          </div>
        )}
        <div className="flex flex-wrap gap-1.5">
          <Chip>持有标签页 · {status.ownedTabCount}</Chip>
          <GroupChip group={status.group} />
        </div>
      </div>
    </section>
  );
}
