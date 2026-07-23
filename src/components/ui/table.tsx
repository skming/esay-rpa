import type { ComponentPropsWithoutRef, ReactElement } from 'react';
import { forwardRef } from 'react';

import { cn } from '../../lib/utils';

const Table = forwardRef<HTMLTableElement, ComponentPropsWithoutRef<'table'>>(
  ({ className, ...props }, ref): ReactElement => (
    <div className="relative w-full overflow-x-auto">
      <table className={cn('w-full caption-bottom text-sm', className)} ref={ref} {...props} />
    </div>
  )
);
Table.displayName = 'Table';

const TableHeader = forwardRef<HTMLTableSectionElement, ComponentPropsWithoutRef<'thead'>>(
  ({ className, ...props }, ref): ReactElement => (
    <thead className={cn('[&_tr]:border-b [&_tr]:border-slate-100', className)} ref={ref} {...props} />
  )
);
TableHeader.displayName = 'TableHeader';

const TableBody = forwardRef<HTMLTableSectionElement, ComponentPropsWithoutRef<'tbody'>>(
  ({ className, ...props }, ref): ReactElement => (
    <tbody className={cn('[&_tr:last-child]:border-0', className)} ref={ref} {...props} />
  )
);
TableBody.displayName = 'TableBody';

const TableFooter = forwardRef<HTMLTableSectionElement, ComponentPropsWithoutRef<'tfoot'>>(
  ({ className, ...props }, ref): ReactElement => (
    <tfoot className={cn('border-t bg-slate-100/50 font-medium [&>tr]:last:border-b-0', className)} ref={ref} {...props} />
  )
);
TableFooter.displayName = 'TableFooter';

const TableRow = forwardRef<HTMLTableRowElement, ComponentPropsWithoutRef<'tr'>>(
  ({ className, ...props }, ref): ReactElement => (
    <tr
      className={cn('border-b border-slate-100 transition-colors hover:bg-slate-50/70 data-[state=selected]:bg-slate-100', className)}
      ref={ref}
      {...props}
    />
  )
);
TableRow.displayName = 'TableRow';

const TableHead = forwardRef<HTMLTableCellElement, ComponentPropsWithoutRef<'th'>>(
  ({ className, ...props }, ref): ReactElement => (
    <th
      className={cn('h-10 px-3 text-left align-middle text-[11px] font-medium text-slate-500 whitespace-nowrap has-[[role=checkbox]]:pr-0', className)}
      ref={ref}
      {...props}
    />
  )
);
TableHead.displayName = 'TableHead';

const TableCell = forwardRef<HTMLTableCellElement, ComponentPropsWithoutRef<'td'>>(
  ({ className, ...props }, ref): ReactElement => (
    <td
      className={cn('px-3 py-2 align-middle text-[12px] has-[[role=checkbox]]:pr-0', className)}
      ref={ref}
      {...props}
    />
  )
);
TableCell.displayName = 'TableCell';

const TableCaption = forwardRef<HTMLTableCaptionElement, ComponentPropsWithoutRef<'caption'>>(
  ({ className, ...props }, ref): ReactElement => (
    <caption className={cn('mt-4 text-[11px] text-slate-500', className)} ref={ref} {...props} />
  )
);
TableCaption.displayName = 'TableCaption';

export { Table, TableBody, TableCaption, TableCell, TableFooter, TableHead, TableHeader, TableRow };
