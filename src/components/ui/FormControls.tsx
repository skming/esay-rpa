import { Minus, Plus } from 'lucide-react';
import type { ChangeEventHandler, ReactElement, ReactNode } from 'react';

import { cn } from '../../lib/utils';
import { Button } from './button';
import { Input } from './input';
import { Label } from './label';
import { Switch } from './switch';

export function ToggleSwitch({ checked, label, onCheckedChange }: { checked: boolean; label: string; onCheckedChange?: (checked: boolean) => void }): ReactElement {
  return (
    <div className="flex h-7 w-full items-center justify-between text-[11px] text-slate-600">
      <Label>{label}</Label>
      <Switch aria-label={label} checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  );
}

export function Field({
  label,
  value,
  mono = false,
  placeholder,
  suffix,
  tone = 'default',
  type = 'text',
  title,
  hint,
  onChange,
  onClick,
  readOnly = false
}: {
  label: string;
  value: string;
  mono?: boolean;
  placeholder?: string;
  suffix?: ReactNode;
  tone?: 'default' | 'blue' | 'accent';
  type?: 'text' | 'number';
  title?: string;
  hint?: string;
  onChange?: ChangeEventHandler<HTMLInputElement>;
  onClick?: () => void;
  readOnly?: boolean;
}): ReactElement {
  return (
    <Label className="block">
      <span className="mb-1 block">{label}</span>
      <span className="relative block">
        <Input
          className={cn(mono && 'font-mono text-[11px]', suffix !== undefined && 'pr-8')}
          onChange={onChange}
          onClick={onClick}
          placeholder={placeholder}
          readOnly={readOnly}
          title={title}
          tone={tone}
          type={type}
          value={value}
        />
        {suffix !== undefined && <span className="absolute inset-y-0 right-2 grid place-items-center">{suffix}</span>}
      </span>
      {hint !== undefined && <span className="mt-1 block text-[10px] leading-4 font-normal text-slate-500">{hint}</span>}
    </Label>
  );
}

export function TextareaField({
  label,
  value,
  mono = false,
  placeholder,
  rows = 5,
  hint,
  onChange,
}: {
  label: string;
  value: string;
  mono?: boolean;
  placeholder?: string;
  rows?: number;
  hint?: string;
  onChange?: ChangeEventHandler<HTMLTextAreaElement>;
}): ReactElement {
  return (
    <Label className="block">
      <span className="mb-1 block">{label}</span>
      <textarea
        className={cn(
          'w-full resize-y rounded-md border border-slate-200 bg-white px-3 py-2 text-[11px] leading-relaxed text-slate-700 shadow-sm outline-none transition placeholder:text-slate-500 focus-visible:border-accent-line focus-visible:ring-2 focus-visible:ring-accent-soft',
          mono && 'font-mono',
        )}
        onChange={onChange}
        placeholder={placeholder}
        rows={rows}
        value={value}
      />
      {hint !== undefined && <span className="mt-1 block text-[10px] leading-4 font-normal text-slate-500">{hint}</span>}
    </Label>
  );
}

/** 互斥的 2-3 项分段选择器 */
export function Segmented<T extends string>({
  label,
  onChange,
  options,
  value
}: {
  label?: string;
  onChange?: (value: T) => void;
  options: { label: string; value: T }[];
  value: T;
}): ReactElement {
  return (
    <div>
      {label !== undefined && <Label className="mb-1 block">{label}</Label>}
      <div className="inline-flex w-full rounded-md bg-slate-100 p-0.5" role="tablist">
        {options.map((option) => {
          const active = option.value === value;
          return (
            <button
              aria-selected={active}
              className={cn(
                'flex-1 rounded-md px-3 py-1 text-[11px] font-medium transition-all duration-150',
                active ? 'bg-white text-slate-700 shadow-xs' : 'text-slate-500 hover:text-slate-700'
              )}
              key={option.value}
              onClick={() => onChange?.(option.value)}
              role="tab"
              type="button"
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function NumberField({ label, onChange, onStep, value }: { label: string; onChange?: ChangeEventHandler<HTMLInputElement>; onStep?: (delta: number) => void; value: string }): ReactElement {
  return (
    <div>
      <Label className="mb-1 block">{label}</Label>
      <div className="grid h-8 grid-cols-[32px_1fr_32px] overflow-hidden rounded-md border border-slate-200 bg-white">
        <Button aria-label={`${label} 减少`} className="rounded-none border-0 border-r border-slate-200 text-slate-500 hover:bg-slate-50 hover:text-slate-700" onClick={() => onStep?.(-5)} size="icon" variant="outline">
          <Minus className="h-3.5 w-3.5" strokeWidth={1.5} />
        </Button>
        <Input className="min-w-0 rounded-none border-0 px-2 text-center font-mono text-[11px] focus:ring-0" onChange={onChange} type="number" value={value} />
        <Button aria-label={`${label} 增加`} className="rounded-none border-0 border-l border-slate-200 text-slate-500 hover:bg-slate-50 hover:text-slate-700" onClick={() => onStep?.(5)} size="icon" variant="outline">
          <Plus className="h-3.5 w-3.5" strokeWidth={1.5} />
        </Button>
      </div>
    </div>
  );
}
