import { RefreshCcw } from 'lucide-react';
import type { ReactElement, ReactNode } from 'react';
import { useState } from 'react';

import { cn } from '../../lib/utils';
import { Button, IconButton } from './button';

const MIN_SPIN_MS = 600;

function useSpinning(handler: () => void | Promise<void>): [boolean, () => void] {
  const [spinning, setSpinning] = useState(false);

  const trigger = (): void => {
    if (spinning) return;
    setSpinning(true);
    const minEnd = Date.now() + MIN_SPIN_MS;
    void Promise.resolve(handler()).finally(() => {
      const remaining = minEnd - Date.now();
      if (remaining > 0) {
        setTimeout(() => setSpinning(false), remaining);
      } else {
        setSpinning(false);
      }
    });
  };

  return [spinning, trigger];
}

export function RefreshButton({
  children,
  className,
  onClick,
  variant = 'outline'
}: {
  children?: ReactNode;
  className?: string;
  onClick: () => void | Promise<void>;
  variant?: 'outline' | 'ghost' | 'primary' | 'secondary' | 'danger' | 'soft' | 'ink' | 'ledger';
}): ReactElement {
  const [spinning, trigger] = useSpinning(onClick);

  return (
    <Button className={className} disabled={spinning} onClick={trigger} variant={variant}>
      <RefreshCcw className={cn('h-3.5 w-3.5', spinning && 'animate-spin')} strokeWidth={1.5} />
      {children}
    </Button>
  );
}

export function RefreshIconButton({
  className,
  label = '刷新',
  onClick
}: {
  className?: string;
  label?: string;
  onClick: () => void | Promise<void>;
}): ReactElement {
  const [spinning, trigger] = useSpinning(onClick);

  return (
    <IconButton className={className} disabled={spinning} label={label} onClick={trigger}>
      <RefreshCcw className={cn('h-3.5 w-3.5', spinning && 'animate-spin')} strokeWidth={1.5} />
    </IconButton>
  );
}
