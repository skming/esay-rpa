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
        'flex h-8 w-full rounded-md border border-slate-200 bg-white px-2 text-[11px] text-slate-700 outline-none focus-visible:outline-none transition placeholder:text-slate-400 focus:border-accent focus:ring-2 focus:ring-accent-soft disabled:cursor-not-allowed disabled:opacity-50',
        tone === 'blue' && 'border-blue-200 bg-blue-50 text-blue-800 focus:border-blue-300 focus:ring-blue-100',
        tone === 'accent' && 'border-accent-line bg-accent-wash text-accent-strong focus:border-accent focus:ring-accent-soft',
        className
      )}
      ref={ref}
      type={type}
      {...props}
    />
  )
);

Input.displayName = 'Input';
