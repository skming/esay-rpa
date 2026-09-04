import { cva, type VariantProps } from 'class-variance-authority';
import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';

const badgeVariants = cva('inline-flex shrink-0 items-center whitespace-nowrap rounded-full border px-1.5 py-0.5 text-[10px] font-semibold leading-none tracking-wide', {
  defaultVariants: {
    variant: 'default'
  },
  variants: {
    variant: {
      /* golden-yellow — warning / queued */
      amber: 'border-amber-300/60 bg-amber-50 text-amber-700',
      /* cobalt accent — info / running */
      blue: 'border-accent-line bg-accent-soft text-accent-strong',
      /* neutral */
      default: 'border-slate-200 bg-slate-100 text-slate-500',
      /* mint-green — success / enabled */
      emerald: 'border-emerald-200 bg-emerald-50 text-emerald-700',
      /* error */
      red: 'border-red-200 bg-red-50 text-red-600',
      /* cobalt accent (alias of blue, kept for callers) */
      violet: 'border-accent-line bg-accent-soft text-accent-strong'
    }
  }
});

export type BadgeProps = ComponentProps<'span'> & VariantProps<typeof badgeVariants>;

export function Badge({ className, variant, ...props }: BadgeProps): ReactElement {
  return <span className={cn(badgeVariants({ className, variant }))} data-slot="badge" {...props} />;
}

export { badgeVariants };
