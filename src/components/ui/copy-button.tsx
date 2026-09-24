import { Check, Copy } from 'lucide-react';
import type { ReactElement } from 'react';
import { useState } from 'react';

import { cn } from '../../lib/utils';
import { Button } from './button';

// 复制到剪贴板后短暂显示对勾；日志行、错误摘要等多处复用同一交互
export function CopyButton({ className, text, title = '复制' }: {
  className?: string;
  text: string;
  title?: string;
}): ReactElement {
  const [copied, setCopied] = useState(false);

  const handleCopy = (): void => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <Button className={cn('h-6 w-6 px-0', className)} onClick={handleCopy} title={title} variant="ghost">
      {copied ? <Check className="h-3 w-3 text-emerald-500" strokeWidth={2} /> : <Copy className="h-3 w-3" strokeWidth={1.5} />}
    </Button>
  );
}
