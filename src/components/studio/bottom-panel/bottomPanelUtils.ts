import type { RunLogLevel, RuntimeVariable } from '../../../types/rpa';

export function getLogTone(level: RunLogLevel): { dot: string; row: string; text: string } {
  const tones: Record<RunLogLevel, { dot: string; row: string; text: string }> = {
    error: { dot: 'bg-red-500', row: 'bg-red-50', text: 'text-red-700' },
    info: { dot: 'bg-slate-400', row: 'bg-transparent', text: 'text-slate-600' },
    input: { dot: 'bg-amber-400', row: 'bg-amber-50', text: 'text-amber-800' },
    running: { dot: 'bg-blue-500', row: 'bg-blue-50', text: 'text-blue-700' },
    success: { dot: 'bg-emerald-500', row: 'bg-emerald-50', text: 'text-emerald-700' },
    warn: { dot: 'bg-amber-500', row: 'bg-amber-50', text: 'text-amber-700' }
  };

  return tones[level];
}

export function getScopeVariant(scope: RuntimeVariable['scope']): 'amber' | 'blue' | 'default' {
  if (scope === '循环') return 'amber';
  if (scope === '局部') return 'blue';
  return 'default';
}

export function getTypeVariant(type: RuntimeVariable['type']): 'amber' | 'blue' | 'emerald' | 'red' | 'violet' | 'default' {
  const variants: Record<RuntimeVariable['type'], 'amber' | 'blue' | 'emerald' | 'red' | 'violet' | 'default'> = {
    Boolean: 'amber',
    Dict: 'red',
    Integer: 'violet',
    List: 'emerald',
    String: 'blue'
  };

  return variants[type] ?? 'default';
}

export function getVariableSourceTone(source: 'default' | 'override' | 'runtime'): { badge: string; label: string } {
  const tones: Record<'default' | 'override' | 'runtime', { badge: string; label: string }> = {
    default: { badge: 'border-slate-200 bg-slate-50 text-slate-600', label: '默认值' },
    override: { badge: 'border-blue-200 bg-blue-50 text-blue-700', label: '本次覆写' },
    runtime: { badge: 'border-emerald-200 bg-emerald-50 text-emerald-700', label: '运行时' }
  };

  return tones[source];
}
