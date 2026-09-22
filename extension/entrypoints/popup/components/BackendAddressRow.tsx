export function BackendAddressRow({ backendBaseUrl }: { backendBaseUrl: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-t border-rule pt-3 text-[10px]">
      <span className="shrink-0 text-ink-4">连接目标</span>
      <code className="min-w-0 truncate font-mono text-ink-3">{backendBaseUrl}</code>
    </div>
  );
}
