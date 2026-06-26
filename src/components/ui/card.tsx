import type { HTMLAttributes, ReactElement } from 'react';
import { forwardRef } from 'react';

import { cn } from '../../lib/utils';

const Card = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref): ReactElement => (
    <div
      className={cn(
        'rounded-xl border border-slate-200/70 bg-white',
        className,
      )}
      ref={ref}
      {...props}
    />
  ),
);
Card.displayName = 'Card';

const CardHeader = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref): ReactElement => (
    <div className={cn('flex flex-col space-y-1 p-4', className)} ref={ref} {...props} />
  ),
);
CardHeader.displayName = 'CardHeader';

const CardTitle = forwardRef<HTMLHeadingElement, HTMLAttributes<HTMLHeadingElement>>(
  ({ className, ...props }, ref): ReactElement => (
    <h3 className={cn('text-[12px] font-semibold leading-none tracking-tight text-slate-700', className)} ref={ref} {...props} />
  ),
);
CardTitle.displayName = 'CardTitle';

const CardContent = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref): ReactElement => (
    <div className={cn('p-4 pt-0', className)} ref={ref} {...props} />
  ),
);
CardContent.displayName = 'CardContent';

export { Card, CardContent, CardHeader, CardTitle };
