import { AlertCircle, FileSpreadsheet, Loader2 } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useState } from 'react';

import { backend, type CsvPreviewData } from '../../../lib/backendClient';
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle } from '../../ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../../ui/table';

type PreviewResult = { requestKey: string; data?: CsvPreviewData; error?: string };

export function ExcelPreviewDialog({
  open,
  onOpenChange,
  path,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  path: string;
}): ReactElement {
  const requestKey = open && path.trim() !== '' ? path : null;
  const [result, setResult] = useState<PreviewResult | null>(null);

  useEffect(() => {
    if (requestKey === null) return;
    let cancelled = false;
    backend.previewCsv(path)
      .then((d) => { if (!cancelled) setResult({ data: d, requestKey }); })
      .catch((e: unknown) => { if (!cancelled) setResult({ error: (e as Error).message, requestKey }); });
    return () => { cancelled = true; };
  }, [requestKey, path]);

  // 结果按请求归属而非先清空再拉取：既不会闪一帧上个文件的表格，慢响应迟到也盖不掉新结果
  const current = result?.requestKey === requestKey ? result : null;
  const loading = requestKey !== null && current === null;
  const data = current?.data ?? null;
  const error = current?.error ?? null;

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="flex max-h-[calc(100vh-48px)] w-[900px] max-w-[95vw] flex-col overflow-hidden">
        <DialogHeader className="shrink-0">
          <DialogTitle className="inline-flex items-center gap-2">
            <FileSpreadsheet className="h-4 w-4 text-emerald-600" strokeWidth={1.5} />
            CSV 预览
            {path && <span className="font-mono text-[11px] text-slate-500">{path}</span>}
          </DialogTitle>
        </DialogHeader>
        <DialogBody className="min-h-0 flex-1 overflow-auto">
          {loading && (
            <div className="flex items-center justify-center py-12 text-slate-500">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              <span className="text-[12px]">加载中…</span>
            </div>
          )}
          {error && (
            <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" strokeWidth={1.5} />
              <div>
                <p className="text-[12px] font-medium text-red-700">无法预览文件</p>
                <p className="mt-0.5 text-[11px] text-red-700">{error}</p>
              </div>
            </div>
          )}
          {data && data.headers.length === 0 && (
            <div className="py-8 text-center text-[12px] text-slate-500">文件为空或没有列头</div>
          )}
          {data && data.headers.length > 0 && (
            <div className="space-y-2">
              {/* truncated 及 200 行上限均由后端 preview-csv 接口决定，前端不做二次截断 */}
              {data.truncated && (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-700">
                  仅显示前 200 行，文件共有更多数据
                </div>
              )}
              <div className="rounded-md border border-slate-200">
                <Table>
                  <TableHeader className="sticky top-0 bg-slate-50">
                    <TableRow>
                      <TableHead className="w-8 text-right text-slate-500">#</TableHead>
                      {data.headers.map((h, i) => (
                        <TableHead key={i}>{h}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.rows.map((row, ri) => (
                      <TableRow key={ri}>
                        <TableCell className="text-right text-slate-500">{ri + 1}</TableCell>
                        {data.headers.map((_, ci) => (
                          <TableCell className="max-w-[300px] truncate text-slate-600" key={ci} title={row[ci] ?? ''}>
                            {row[ci] ?? ''}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <p className="text-[10px] text-slate-500">共 {data.total_rows} 行 · {data.headers.length} 列</p>
            </div>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
