import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import type { ButtonHTMLAttributes, ReactElement, ReactNode } from 'react';
import { forwardRef } from 'react';

import { cn } from '../../lib/utils';
import { Tooltip, TooltipContent, TooltipTrigger } from './tooltip';

const buttonVariants = cva(
  'inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg text-[11px] font-medium leading-none transition-all duration-150 active:scale-[0.97] disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-40 disabled:active:scale-100',
  {
    defaultVariants: {
      size: 'default',
      variant: 'ghost'
    },
    variants: {
      size: {
        default: 'h-7 px-2.5',
        icon: 'h-7 w-7 p-0',
        sm: 'h-6 px-2',
        lg: 'h-8 px-3'
      },
      variant: {
        danger: 'bg-red-500 text-white shadow-sm hover:bg-red-600 active:bg-red-700',
        ghost: 'text-slate-500 hover:bg-slate-100 hover:text-slate-800',
        outline: 'border border-slate-200/80 bg-white text-slate-700 shadow-xs hover:bg-slate-50',
        primary: 'bg-brand-gradient text-[var(--color-accent-fg)] shadow-sm hover:opacity-90 active:opacity-80',
        secondary: 'border border-[var(--color-accent-line)] bg-surface text-accent-strong hover:bg-[var(--color-accent-soft)]',
        soft: 'border border-[var(--color-accent-line)] bg-[var(--color-accent-wash)] text-accent-strong hover:bg-[var(--color-accent-soft)]',
        ink: 'bg-ink text-paper hover:bg-slate-700',
        subtle: 'border border-rule-2 bg-surface text-ink-2 hover:bg-paper-sunk hover:text-ink',
      }
    }
  }
);

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  };

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ asChild = false, className, size, type = 'button', variant, ...props }, ref): ReactElement => {
    const Comp = asChild ? Slot : 'button';
    return <Comp className={cn(buttonVariants({ className, size, variant }))} ref={ref} type={type} {...props} />;
  }
);

Button.displayName = 'Button';

export function IconButton({
  active = false,
  children,
  className,
  label,
  ...props
}: Omit<ButtonProps, 'aria-label' | 'children' | 'size'> & {
  active?: boolean;
  children: ReactNode;
  label: string;
}): ReactElement {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          aria-label={label}
          className={cn(
            'text-slate-500 hover:bg-slate-100 hover:text-slate-700',
            active && 'bg-accent-soft text-accent-strong',
            className
          )}
          size="icon"
          variant="ghost"
          {...props}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
}

export { buttonVariants };
