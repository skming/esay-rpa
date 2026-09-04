import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';

function Table({ className, ...props }: ComponentProps<'table'>): ReactElement {
  return (
    <div className="relative w-full overflow-x-auto" data-slot="table-container">
      <table className={cn('w-full caption-bottom text-sm', className)} data-slot="table" {...props} />
    </div>
  );
}

function TableHeader({ className, ...props }: ComponentProps<'thead'>): ReactElement {
  return <thead className={cn('[&_tr]:border-b [&_tr]:border-slate-100', className)} data-slot="table-header" {...props} />;
}

function TableBody({ className, ...props }: ComponentProps<'tbody'>): ReactElement {
  return <tbody className={cn('[&_tr:last-child]:border-0', className)} data-slot="table-body" {...props} />;
}

function TableFooter({ className, ...props }: ComponentProps<'tfoot'>): ReactElement {
  return (
    <tfoot
      className={cn('border-t bg-slate-100/50 font-medium [&>tr]:last:border-b-0', className)}
      data-slot="table-footer"
      {...props}
    />
  );
}

function TableRow({ className, ...props }: ComponentProps<'tr'>): ReactElement {
  return (
    <tr
      className={cn('border-b border-slate-100 transition-colors hover:bg-slate-50/70 data-[state=selected]:bg-slate-100', className)}
      data-slot="table-row"
      {...props}
    />
  );
}

function TableHead({ className, ...props }: ComponentProps<'th'>): ReactElement {
  return (
    <th
      className={cn('h-10 px-3 text-left align-middle text-[11px] font-medium text-slate-500 whitespace-nowrap has-[[role=checkbox]]:pr-0', className)}
      data-slot="table-head"
      {...props}
    />
  );
}

function TableCell({ className, ...props }: ComponentProps<'td'>): ReactElement {
  return (
    <td
      className={cn('px-3 py-2 align-middle text-[12px] has-[[role=checkbox]]:pr-0', className)}
      data-slot="table-cell"
      {...props}
    />
  );
}

function TableCaption({ className, ...props }: ComponentProps<'caption'>): ReactElement {
  return <caption className={cn('mt-4 text-[11px] text-slate-500', className)} data-slot="table-caption" {...props} />;
}

export { Table, TableBody, TableCaption, TableCell, TableFooter, TableHead, TableHeader, TableRow };
