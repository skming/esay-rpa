export function BackendAddressRow({ backendBaseUrl }: { backendBaseUrl: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-rule px-3 py-2 text-xs shadow-xs">
      <span className="shrink-0 text-ink-4">后端地址</span>
      <span className="min-w-0 truncate font-mono text-ink-3">{backendBaseUrl}</span>
    </div>
  );
}
