export function BridgeStatusCard({ connected }: { connected: boolean }) {
  return (
    <section className="rounded-lg border border-rule-2 bg-paper-sunk/70 p-3">
      <div className="relative flex items-center gap-2">
        <span
          className={[
            'relative h-2 w-2 shrink-0 rounded-full',
            connected
              ? 'bg-live shadow-running after:absolute after:-inset-1.25 after:rounded-full after:border-[1.5px] after:border-live after:animate-[bridge-live-ping_1.9s_cubic-bezier(0.16,1,0.3,1)_infinite] motion-reduce:after:animate-none'
              : 'bg-ink-4',
          ].join(' ')}
        />
        <span className="text-sm font-medium text-ink-2">
          {connected ? '已连接到 Easy RPA' : '未连接'}
        </span>
      </div>
      <p className="relative mt-1.5 text-xs leading-relaxed text-ink-3">
        {connected
          ? '浏览器插件已进入可执行状态，运行流程时可直接接管当前网页。'
          : '请确认 Easy RPA 应用已启动，启动后会自动连接，无需重装插件。'}
      </p>
    </section>
  );
}
