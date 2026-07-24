import { Crosshair, Gauge, Loader2 } from 'lucide-react';
import type { ReactElement } from 'react';

import type { ElectronBridgeState } from '../../../../hooks/useElectronBridge';
import { Badge } from '../../../ui/badge';
import { Button } from '../../../ui/button';
import { Input } from '../../../ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../ui/select';
import type { ExtractMode } from '../../../../types/rpa';
import { LabelLike } from './FieldLayout';

/** browser.* / ui.* 里复用的取值控件与提示条，从 BrowserActionFields 的分支路由中拆出。 */

export function ExtractModeField({ onChange, value }: { onChange: (value: ExtractMode) => void; value: ExtractMode }): ReactElement {
  return (
    <LabelLike text="提取方式">
      <Select onValueChange={(next) => onChange(next as ExtractMode)} value={value}>
        <SelectTrigger className="font-mono text-[11px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="text">文本</SelectItem>
          <SelectItem value="html">HTML</SelectItem>
          <SelectItem value="attribute">属性</SelectItem>
          <SelectItem value="count">数量</SelectItem>
          <SelectItem value="table">表格</SelectItem>
        </SelectContent>
      </Select>
    </LabelLike>
  );
}

export function CheckedStateField({ onChange, value }: { onChange: (value: boolean) => void; value: boolean }): ReactElement {
  return (
    <LabelLike text="复选状态">
      <Select onValueChange={(next) => onChange(next === 'true')} value={String(value)}>
        <SelectTrigger className="font-mono text-[11px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="true">选中</SelectItem>
          <SelectItem value="false">取消选中</SelectItem>
        </SelectContent>
      </Select>
    </LabelLike>
  );
}

export function SelectorField({
  electron,
  label,
  onChange,
  targetUrl,
  value
}: {
  electron: ElectronBridgeState;
  label: string;
  onChange: (value: string) => void;
  targetUrl?: string;
  value: string;
}): ReactElement {
  const effectiveUrl = targetUrl?.trim() || undefined;

  const picking = electron.pickerActive;

  // 拾取器依赖已解析的目标网址（本节点 targetUrl 或流程级 flowTargetUrl），缺失时静默拒绝并仅提示，不抛错
  const handlePickerClick = (): void => {
    if (picking) return;
    if (!effectiveUrl) {
      electron.pushToast('info', '请先在"打开网页"节点配置目标页面地址');
      return;
    }
    void electron.openPicker(effectiveUrl);
  };

  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium text-slate-600">{label}</span>
        <Button
          className="h-6 px-2 text-indigo-500"
          disabled={picking}
          onClick={handlePickerClick}
          title={picking ? '拾取器已打开，请在浏览器中点击元素' : effectiveUrl ? `启动拾取器（${effectiveUrl}）` : '启动元素拾取器'}
          variant="ghost"
        >
          {picking ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.5} />
          ) : (
            <Crosshair className="h-3.5 w-3.5" strokeWidth={1.5} />
          )}
          {picking ? '拾取中…' : '拾取'}
        </Button>
      </div>
      <Input className="font-mono text-[11px]" onChange={(event) => onChange(event.target.value)} tone="blue" value={value} />
      {effectiveUrl && (
        <p className="mt-0.5 truncate font-mono text-[10px] text-slate-500" title={effectiveUrl}>
          {effectiveUrl}
        </p>
      )}
    </div>
  );
}

export function InlineHint({ text, tone = 'default' }: { text: string; tone?: 'default' | 'warn' }): ReactElement {
  return (
    <div className={`rounded-md border px-2 py-1.5 text-[10px] leading-4 ${tone === 'warn' ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-slate-200 bg-slate-50 text-slate-500'}`}>
      {text}
    </div>
  );
}

export function SiteAnalysisSummary({ analysis, onSelect }: { analysis: NonNullable<ElectronBridgeState['siteAnalysis']>; onSelect: (selector: string) => void }): ReactElement {
  return (
    <div className="space-y-2 rounded-md border border-slate-200 bg-slate-50 p-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5 text-[11px] font-medium text-slate-700">
          <Gauge className="h-3.5 w-3.5 text-blue-500" strokeWidth={1.5} />
          <span className="truncate">{analysis.title ?? '站点分析'}</span>
        </div>
        <Badge variant={analysis.riskLevel === 'high' ? 'red' : analysis.riskLevel === 'medium' ? 'amber' : 'emerald'}>
          {analysis.riskLevel === 'high' ? '高风险' : analysis.riskLevel === 'medium' ? '中风险' : '低风险'}
        </Badge>
      </div>
      {analysis.checkedSelector !== null && analysis.checkedSelector !== undefined && (
        <div className="font-mono text-[10px] text-slate-500">
          当前命中 {analysis.checkedSelector.matchCount} 个元素 · {analysis.checkedSelector.stable ? '稳定' : '需优化'}
        </div>
      )}
      {analysis.warnings.slice(0, 2).map((warning) => (
        <div className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-[10px] leading-4 text-amber-800" key={warning}>
          {warning}
        </div>
      ))}
      <div className="space-y-1">
        {analysis.candidates.slice(0, 3).map((candidate) => (
          <Button
            className="flex h-auto w-full items-center justify-between gap-2 rounded border border-slate-200 bg-white px-2 py-1.5 text-left hover:border-blue-200 hover:bg-blue-50"
            key={candidate.selector}
            onClick={() => onSelect(candidate.selector)}
            title={candidate.reasons.join('；')}
            variant="ghost"
          >
            <span className="min-w-0 truncate font-mono text-[10px] text-slate-700">{candidate.selector}</span>
            <span className="shrink-0 text-[10px] text-blue-600">{candidate.stabilityScore}</span>
          </Button>
        ))}
      </div>
    </div>
  );
}
