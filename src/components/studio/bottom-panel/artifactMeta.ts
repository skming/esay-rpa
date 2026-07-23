import { Database, FileJson, FileText, ImageIcon, ScrollText } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import type { ArtifactSnapshot } from '../../../types/electron';

export function getArtifactMeta(type: ArtifactSnapshot['artifactType']): {
  icon: LucideIcon;
  iconTone: string;
  label: string;
  variant: 'amber' | 'blue' | 'emerald' | 'red' | 'violet' | 'default';
} {
  const meta: Record<
    ArtifactSnapshot['artifactType'],
    { icon: LucideIcon; iconTone: string; label: string; variant: 'amber' | 'blue' | 'emerald' | 'red' | 'violet' | 'default' }
  > = {
    dataset: { icon: Database, iconTone: 'text-emerald-500', label: '数据集', variant: 'emerald' },
    log: { icon: ScrollText, iconTone: 'text-slate-500', label: '日志', variant: 'default' },
    report: { icon: FileText, iconTone: 'text-blue-500', label: '报告', variant: 'blue' },
    screenshot: { icon: ImageIcon, iconTone: 'text-accent', label: '截图', variant: 'violet' },
    script: { icon: FileJson, iconTone: 'text-amber-500', label: '脚本', variant: 'amber' },
  };
  return meta[type] ?? meta.dataset;
}
