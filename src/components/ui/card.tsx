import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';

function Card({ className, ...props }: ComponentProps<'div'>): ReactElement {
  return (
    <div
      className={cn(
        'rounded-xl border border-slate-200/70 bg-white',
        className,
      )}
      data-slot="card"
      {...props}
    />
  );
}

function CardHeader({ className, ...props }: ComponentProps<'div'>): ReactElement {
  return <div className={cn('flex flex-col space-y-1 p-4', className)} data-slot="card-header" {...props} />;
}

function CardTitle({ className, ...props }: ComponentProps<'h3'>): ReactElement {
  return (
    <h3
      className={cn('text-[12px] font-semibold leading-none tracking-tight text-slate-700', className)}
      data-slot="card-title"
      {...props}
    />
  );
}

function CardContent({ className, ...props }: ComponentProps<'div'>): ReactElement {
  return <div className={cn('p-4 pt-0', className)} data-slot="card-content" {...props} />;
}

export { Card, CardContent, CardHeader, CardTitle };
