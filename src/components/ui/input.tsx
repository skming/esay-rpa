import type { InputHTMLAttributes, ReactElement } from 'react';
import { forwardRef } from 'react';

import { cn } from '../../lib/utils';

export type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  tone?: 'default' | 'blue' | 'accent';
};

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, tone = 'default', type = 'text', ...props }, ref): ReactElement => (
    <input
      className={cn(
        'flex h-8 w-full rounded-md border border-slate-200 bg-white px-2 text-[11px] text-slate-700 outline-none transition placeholder:text-slate-500 focus-visible:border-accent-line focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-soft disabled:cursor-not-allowed disabled:opacity-50',
        tone === 'blue' && 'border-blue-200 bg-blue-50 text-blue-800 focus-visible:border-live-line focus-visible:ring-live-soft',
        tone === 'accent' && 'border-accent-line bg-accent-wash text-accent-strong focus-visible:border-accent-line focus-visible:ring-accent-soft',
        'aria-invalid:border-red-200 aria-invalid:bg-red-50 aria-invalid:text-red-700 aria-invalid:focus-visible:border-red-300 aria-invalid:focus-visible:ring-red-100',
        className
      )}
      ref={ref}
      type={type}
      {...props}
    />
  )
);

Input.displayName = 'Input';
