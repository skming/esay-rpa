import { AlertTriangle } from 'lucide-react';
import type { ErrorInfo, ReactElement, ReactNode } from 'react';
import { Component } from 'react';

import { Button } from '../ui/button';

type ErrorBoundaryProps = { children: ReactNode };
type ErrorBoundaryState = { error: Error | null };

// 桌面应用没有「刷新页面」这个逃生口，未捕获的渲染异常会白屏，用户只能杀进程
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 保留到控制台，便于用户在「帮助 → 开发者工具」里回捞堆栈反馈问题
    console.error('[ErrorBoundary] 渲染异常', error, info.componentStack);
  }

  private handleReload = (): void => {
    window.location.reload();
  };

  private handleDismiss = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (error === null) {
      return this.props.children;
    }
    return <ErrorFallback error={error} onDismiss={this.handleDismiss} onReload={this.handleReload} />;
  }
}

function ErrorFallback({
  error,
  onDismiss,
  onReload,
}: {
  error: Error;
  onDismiss: () => void;
  onReload: () => void;
}): ReactElement {
  return (
    <div className="flex h-screen w-screen items-center justify-center bg-paper-sunk p-8">
      <div className="grid w-full max-w-150 gap-4 rounded-lg border border-rule bg-surface p-6 shadow-sm">
        <div className="flex items-center gap-2.5">
          <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" strokeWidth={1.75} />
          <h1 className="text-[13px] font-semibold text-ink">界面遇到未预期的错误</h1>
        </div>

        <p className="text-[11px] leading-relaxed text-ink-3">
          已保存的流程不受影响。可以先尝试返回继续操作；若界面仍不正常，请重新载入窗口。
        </p>

        <pre className="max-h-50 overflow-auto rounded-md border border-rule bg-paper-sunk p-3 font-mono text-[10px] leading-relaxed break-all whitespace-pre-wrap text-ink-2">
          {error.message || String(error)}
        </pre>

        <div className="flex items-center justify-end gap-2">
          <Button className="h-8 rounded-md px-4 text-[11px]" onClick={onDismiss} variant="subtle">
            返回继续
          </Button>
          <Button className="h-8 rounded-md px-4 text-[11px]" onClick={onReload} variant="primary">
            重新载入
          </Button>
        </div>
      </div>
    </div>
  );
}
