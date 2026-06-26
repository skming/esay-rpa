import type { ReactElement } from 'react';

export function BackendBootScreen({
  error,
  onRetry,
}: {
  error: string | null;
  onRetry: () => void;
}): ReactElement {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 bg-slate-50">
      {error === null ? (
        <>
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-indigo-500" />
          <p className="text-xs text-slate-400">正在启动后端服务...</p>
        </>
      ) : (
        <>
          <p className="text-xs font-medium text-slate-600">后端启动失败</p>
          <p className="max-w-xs text-center text-[11px] leading-relaxed text-slate-400">{error}</p>
          <button
            className="mt-1 rounded-lg bg-indigo-500 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-indigo-600"
            onClick={onRetry}
            type="button"
          >
            重试
          </button>
        </>
      )}
    </div>
  );
}
