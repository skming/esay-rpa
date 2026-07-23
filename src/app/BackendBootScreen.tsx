import { AlertTriangle, Loader2 } from 'lucide-react';
import type { ReactElement } from 'react';

import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { Collapsible } from '../components/ui/collapsible';
import { CodeBlock } from '../components/ui/CodeBlock';

export function BackendBootScreen({
  error,
  installingBrowser = false,
  installProgress = null,
  installStep = null,
  installStepLabel = null,
  installStepTotal = null,
  onRetry,
}: {
  error: string | null;
  installingBrowser?: boolean;
  installProgress?: number | null;
  installStep?: number | null;
  installStepLabel?: string | null;
  installStepTotal?: number | null;
  onRetry: () => void;
}): ReactElement {
  if (error === null) {
    const showProgressBar = installingBrowser && installProgress !== null;
    // Playwright 分批下载多个产物，各自 0→100%，百分比会在一次安装里跳回 0 好几次，因此需直接点名当前下载项
    const stepSuffix = installStepTotal !== null && installStep !== null ? `（${installStep}/${installStepTotal}）` : '';
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 bg-slate-50">
        {showProgressBar ? (
          <div className="w-full max-w-55">
            <div
              aria-label="浏览器组件下载进度"
              aria-valuemax={100}
              aria-valuemin={0}
              aria-valuenow={installProgress}
              className="h-1 w-full overflow-hidden rounded-full bg-slate-200"
              role="progressbar"
            >
              <div
                className="h-full rounded-full bg-indigo-500 transition-all duration-300"
                style={{ width: `${installProgress}%` }}
              />
            </div>
          </div>
        ) : (
          <Loader2 className="h-4 w-4 animate-spin text-indigo-500" strokeWidth={2} />
        )}
        <p className="text-xs text-slate-500">
          {installingBrowser
            ? installStepLabel !== null
              ? `首次启动，正在下载 ${installStepLabel}${stepSuffix}${showProgressBar ? ` ${installProgress}%` : ''}`
              : '首次启动，正在准备浏览器组件...'
            : '正在启动后端服务...'}
        </p>
      </div>
    );
  }

  const lines = error.trim().split('\n').filter((line) => line.trim().length > 0);
  const isLong = lines.length > 3;

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 bg-slate-50 px-6">
      <Card className="w-full max-w-md">
        <CardContent className="flex flex-col gap-3 p-4">
          <div className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-red-100 bg-red-50 text-red-600">
              <AlertTriangle className="h-4 w-4" strokeWidth={1.5} />
            </span>
            <div className="min-w-0">
              <p className="text-xs font-medium text-slate-700">后端启动失败</p>
              <p className="truncate text-[11px] text-slate-500">{lines[0] ?? error}</p>
            </div>
          </div>

          {isLong && (
            <Collapsible title="错误详情">
              <CodeBlock code={error} maxHeight={200} variant="light" wrap />
            </Collapsible>
          )}

          <Button className="self-end" onClick={onRetry} variant="primary">
            重试
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
