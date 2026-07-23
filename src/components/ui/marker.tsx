import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import type { ComponentProps } from 'react';

import { cn } from '../../lib/utils';

const markerVariants = cva(
  "group/marker relative flex min-h-4 w-full items-center gap-1.5 text-left text-[11px] text-slate-500 [&_svg:not([class*='size-'])]:size-3.5",
  {
    variants: {
      variant: {
        default: '',
        separator:
          'before:mr-1 before:h-px before:min-w-0 before:flex-1 before:bg-slate-200 after:ml-1 after:h-px after:min-w-0 after:flex-1 after:bg-slate-200',
        border: 'border-b border-slate-100 pb-2',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
);

export function Marker({
  asChild = false,
  className,
  variant,
  ...props
}: ComponentProps<'div'> & VariantProps<typeof markerVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : 'div';
  return (
    <Comp
      className={cn(markerVariants({ variant, className }))}
      data-slot="marker"
      data-variant={variant}
      {...props}
    />
  );
}

export function MarkerIcon({ className, ...props }: ComponentProps<'span'>) {
  return (
    <span
      aria-hidden="true"
      className={cn("size-3.5 shrink-0 [&_svg:not([class*='size-'])]:size-3.5", className)}
      data-slot="marker-icon"
      {...props}
    />
  );
}

export function MarkerContent({ className, ...props }: ComponentProps<'span'>) {
  return (
    <span
      className={cn(
        'min-w-0 truncate group-data-[variant=separator]/marker:flex-none group-data-[variant=separator]/marker:text-center',
        className
      )}
      data-slot="marker-content"
      {...props}
    />
  );
}

export function ThinkingDots({ className, size = 'sm' }: { className?: string; size?: 'sm' | 'md' }) {
  const dot = size === 'md' ? 'h-1.5 w-1.5' : 'h-1 w-1';
  return (
    <span className={cn('flex items-center gap-1', className)}>
      <span className={cn('animate-thinking-dot rounded-full bg-current [animation-delay:0ms]', dot)} />
      <span className={cn('animate-thinking-dot rounded-full bg-current [animation-delay:150ms]', dot)} />
      <span className={cn('animate-thinking-dot rounded-full bg-current [animation-delay:300ms]', dot)} />
    </span>
  );
}
