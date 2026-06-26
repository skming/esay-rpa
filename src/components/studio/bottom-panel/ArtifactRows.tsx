import { Database, Eye } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useState } from 'react';

import type { ArtifactContent, ArtifactSnapshot } from '../../../types/electron';
import { Badge } from '../../ui/badge';
import { IconButton } from '../../ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../../ui/table';
import { ArtifactPreviewDialog } from './ArtifactPreviewDialog';
import { getArtifactMeta } from './artifactMeta';
import { PanelEmptyState } from './PanelEmptyState';

type ArtifactRowsProps = {
  artifactContent: ArtifactContent | null;
  lastRunId: string | null;
  isMockRun?: boolean;
  rows: ArtifactSnapshot[];
  onReadArtifact: (taskId: string, artifactId: string) => void;
};

export function ArtifactRows({ artifactContent, isMockRun, lastRunId, rows, onReadArtifact }: ArtifactRowsProps): ReactElement {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [previewingId, setPreviewingId] = useState<string | null>(null);
  const [localContent, setLocalContent] = useState<ArtifactContent | null>(null);
  const [localLoading, setLocalLoading] = useState(false);

  useEffect(() => {
    if (previewingId !== null && artifactContent?.artifact.artifactId === previewingId) {
      setLocalContent(artifactContent);
      setLocalLoading(false);
    }
  }, [artifactContent, previewingId]);

  const handlePreview = (artifact: ArtifactSnapshot): void => {
    setPreviewingId(artifact.artifactId);
    setLocalContent(null);
    setLocalLoading(true);
    setDialogOpen(true);
    onReadArtifact(artifact.taskId, artifact.artifactId);
  };

  if (rows.length === 0) {
    const emptyText =
      lastRunId === null
        ? '运行流程后显示采集结果'
        : isMockRun
          ? '当前为模拟运行，无真实采集结果。请确认后端服务已启动后重新运行。'
          : '当前运行未产生采集结果。如流程有截图或文件写入节点，请确认后端服务正常。';
    return <PanelEmptyState icon={Database} text={emptyText} />;
  }

  const previewingArtifact = rows.find((r) => r.artifactId === previewingId) ?? null;

  return (
    <>
      <Table className="table-fixed">
        <TableHeader className="sticky top-0 z-10 bg-white">
          <TableRow>
            <TableHead className="pl-2">文件名</TableHead>
            <TableHead className="w-24">类型</TableHead>
            <TableHead className="w-22">大小</TableHead>
            <TableHead className="w-12 pr-2">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((artifact) => {
            const meta = getArtifactMeta(artifact.artifactType);
            const isActive = previewingId === artifact.artifactId && dialogOpen;
            return (
              <TableRow
                className={isActive ? 'border-blue-200 bg-blue-50' : ''}
                key={artifact.artifactId}
              >
                <TableCell className="pl-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <meta.icon className={`h-3.5 w-3.5 shrink-0 ${meta.iconTone}`} strokeWidth={1.5} />
                    <div className="min-w-0">
                      <div className={`truncate font-mono text-[11px] ${isActive ? 'text-blue-700' : 'text-slate-700'}`}>{artifact.filename}</div>
                      <div className="truncate font-mono text-[10px] text-slate-400">{artifact.artifactId}</div>
                    </div>
                  </div>
                </TableCell>
                <TableCell>
                  <Badge className="w-fit" variant={meta.variant}>{meta.label}</Badge>
                </TableCell>
                <TableCell className="font-mono text-[11px] text-slate-500">{formatBytes(artifact.sizeBytes)}</TableCell>
                <TableCell className="pr-2">
                  <IconButton
                    active={isActive}
                    className="h-7 w-7"
                    label="预览"
                    onClick={() => handlePreview(artifact)}
                  >
                    <Eye className="h-3.5 w-3.5" strokeWidth={1.5} />
                  </IconButton>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      <ArtifactPreviewDialog
        artifact={previewingArtifact}
        content={localContent}
        loading={localLoading}
        onOpenChange={setDialogOpen}
        open={dialogOpen}
      />
    </>
  );
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return '0 B';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
