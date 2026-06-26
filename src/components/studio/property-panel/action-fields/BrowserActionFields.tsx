import { Crosshair, Gauge, Loader2, SearchCheck } from 'lucide-react';
import type { ReactElement } from 'react';

import type { ElectronBridgeState } from '../../../../hooks/useElectronBridge';
import { Badge } from '../../../ui/badge';
import { Button } from '../../../ui/button';
import { Field, Segmented } from '../../../ui/FormControls';
import { Input } from '../../../ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../ui/select';
import type { ExtractMode } from '../../../../types/rpa';
import { VariableNameField } from '../VariableNameField';
import { VariablePickerField } from '../VariablePickerField';
import { LabelLike } from './FieldLayout';
import type { ActionFieldsProps } from './types';

export function BrowserActionFields({ draft, electron, flowTargetUrl, node, onDraftPatch }: ActionFieldsProps): ReactElement {
  const actionType = node.data.action?.type ?? `${node.data.kind}.step`;
  const resolvedTargetUrl = draft.targetUrl?.trim() || flowTargetUrl;
  const availableVariables = electron.variableViews;
  if (actionType === 'browser.clickLoadMore' || actionType === 'browser.paginateNext') {
    const isNextPagination = actionType === 'browser.paginateNext';
    return (
      <>
        <SelectorField
          electron={electron}
          label={isNextPagination ? '下一页按钮 (CSS)' : '加载按钮 (CSS)'}
          onChange={(value) => onDraftPatch('selector', value)}
          targetUrl={resolvedTargetUrl}
          value={draft.selector}
        />
        <Field label="列表项选择器" mono onChange={(event) => onDraftPatch('targetSelector', event.target.value)} placeholder=".item::text" value={draft.targetSelector} />
        <LabelLike text="提取方式">
          <Select onValueChange={(value) => onDraftPatch('extractMode', value as ExtractMode)} value={draft.extractMode}>
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
        {draft.extractMode === 'attribute' && <Field label="属性名" mono onChange={(event) => onDraftPatch('attribute', event.target.value)} placeholder="href" value={draft.attribute} />}
        <Field label={isNextPagination ? '最大页数' : '最大点击次数'} onChange={(event) => onDraftPatch('maxIterations', Math.max(1, Number.parseInt(event.target.value, 10) || 1))} type="number" value={String(draft.maxIterations)} />
        <Field label="点击后等待(ms)" onChange={(event) => onDraftPatch('delayMs', Math.max(0, Number.parseInt(event.target.value, 10) || 0))} type="number" value={String(draft.delayMs)} />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder={isNextPagination ? 'paged_items' : 'loaded_items'} value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }
  if (actionType === 'browser.dismiss') {
    return (
      <>
        <SelectorField
          electron={electron}
          label="弹窗候选选择器"
          onChange={(value) => onDraftPatch('selector', value)}
          targetUrl={resolvedTargetUrl}
          value={draft.selector}
        />
        <Field label="关闭后等待目标" mono onChange={(event) => onDraftPatch('targetSelector', event.target.value)} placeholder=".content-ready" value={draft.targetSelector} />
        <Field label="最大尝试次数" onChange={(event) => onDraftPatch('maxIterations', Math.max(1, Number.parseInt(event.target.value, 10) || 1))} type="number" value={String(draft.maxIterations)} />
        <Field label="点击后等待(ms)" onChange={(event) => onDraftPatch('delayMs', Math.max(0, Number.parseInt(event.target.value, 10) || 0))} type="number" value={String(draft.delayMs)} />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="dismiss_result" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }
  if (actionType === 'browser.scroll') {
    return <Field label="滚动距离" onChange={(event) => onDraftPatch('distance', Number.parseInt(event.target.value, 10) || 0)} type="number" value={String(draft.distance)} />;
  }
  if (actionType === 'browser.press') {
    return (
      <>
        <SelectorField electron={electron} label="输入控件 (CSS)" onChange={(value) => onDraftPatch('selector', value)} targetUrl={resolvedTargetUrl} value={draft.selector} />
        <Field label="按键" mono onChange={(event) => onDraftPatch('inputValue', event.target.value)} placeholder="Enter" value={draft.inputValue} />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="search_submit_key" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }
  if (actionType === 'browser.tab.switch') {
    return <Field label="标签页索引" onChange={(event) => onDraftPatch('tabIndex', Math.max(0, Number.parseInt(event.target.value, 10) || 0))} type="number" value={String(draft.tabIndex)} />;
  }
  if (actionType === 'browser.tab.close' || actionType === 'browser.screenshot') {
    return <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="browser_result" value={draft.responseVariable} variables={availableVariables} />;
  }
  if (actionType === 'browser.select') {
    return (
      <>
        <SelectorField electron={electron} label="下拉元素 (CSS)" onChange={(value) => onDraftPatch('selector', value)} targetUrl={resolvedTargetUrl} value={draft.selector} />
        <VariablePickerField label="选项值" onChange={(value) => onDraftPatch('inputValue', value)} value={draft.inputValue} variables={availableVariables} />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="selected_value" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }
  if (actionType === 'browser.check') {
    return (
      <>
        <SelectorField electron={electron} label="复选元素 (CSS)" onChange={(value) => onDraftPatch('selector', value)} targetUrl={resolvedTargetUrl} value={draft.selector} />
        <LabelLike text="复选状态">
          <Select onValueChange={(value) => onDraftPatch('checked', value === 'true')} value={String(draft.checked)}>
            <SelectTrigger className="font-mono text-[11px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="true">选中</SelectItem>
              <SelectItem value="false">取消选中</SelectItem>
            </SelectContent>
          </Select>
        </LabelLike>
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="check_result" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }

  const selectorLabel = node.data.kind === 'ui' ? '目标控件 (CSS)' : '目标元素 (CSS)';
  const usesSelector = shouldShowSelectorControls(actionType);
  return (
    <>
      {(actionType === 'browser.fetch' || actionType === 'browser.open' || actionType === 'browser.tab.open') && (
        <Field
          label="目标网址"
          mono
          onChange={(event) => onDraftPatch('targetUrl', event.target.value)}
          placeholder="https://quotes.toscrape.com/"
          value={draft.targetUrl}
        />
      )}
      {usesSelector && (
        <SelectorField electron={electron} label={selectorLabel} onChange={(value) => onDraftPatch('selector', value)} targetUrl={resolvedTargetUrl} value={draft.selector} />
      )}
      {(actionType === 'browser.fetch' || actionType === 'browser.fill' || actionType === 'ui.fill') && (
        <InlineHint
          tone={readBrowserHintTone(actionType, draft)}
          text={readBrowserHintText(actionType, draft)}
        />
      )}
      {usesSelector && (
        <>
          <Button className="w-full" onClick={() => void electron.analyzeCurrentSite()} variant="outline">
            <SearchCheck className="h-3.5 w-3.5" strokeWidth={1.5} />
            分析选择器稳定性
          </Button>
          {electron.siteAnalysis !== null && <SiteAnalysisSummary analysis={electron.siteAnalysis} onSelect={(selector) => onDraftPatch('selector', selector)} />}
        </>
      )}
      {(actionType === 'browser.extract' || actionType === 'ui.extract' || actionType === 'browser.fetch') && (
        <LabelLike text="提取方式">
          <Select onValueChange={(value) => onDraftPatch('extractMode', value as ExtractMode)} value={draft.extractMode}>
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
      )}
      {(actionType === 'browser.extract' || actionType === 'ui.extract' || actionType === 'browser.fetch') && draft.extractMode === 'attribute' && (
        <Field label="属性名" mono onChange={(event) => onDraftPatch('attribute', event.target.value)} placeholder="href" value={draft.attribute} />
      )}
      {(actionType === 'browser.fill' || actionType === 'ui.fill' || actionType === 'ui.select') && <VariablePickerField onChange={(value) => onDraftPatch('inputValue', value)} value={draft.inputValue} variables={availableVariables} />}
      {(actionType === 'browser.extract' || actionType === 'ui.extract' || actionType === 'ui.screenshot' || actionType === 'ui.select' || actionType === 'ui.check' || actionType === 'ui.drag') && (
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="ui_values" value={draft.responseVariable} variables={availableVariables} />
      )}
      {actionType === 'ui.drag' && <Field label="目标控件" mono onChange={(event) => onDraftPatch('targetSelector', event.target.value)} placeholder="#target" value={draft.targetSelector} />}
      {actionType === 'ui.check' && (
        <LabelLike text="复选状态">
          <Select onValueChange={(value) => onDraftPatch('checked', value === 'true')} value={String(draft.checked)}>
            <SelectTrigger className="font-mono text-[11px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="true">选中</SelectItem>
              <SelectItem value="false">取消选中</SelectItem>
            </SelectContent>
          </Select>
        </LabelLike>
      )}
      {(actionType === 'browser.fill' || actionType === 'ui.fill') && (
        <Segmented
          label="输入方式"
          onChange={(value) => onDraftPatch('fillMode', value === 'assign' ? 'js' : value === 'type' ? 'type' : 'fill')}
          options={[
            { label: '标准填充', value: 'fill' },
            { label: '键盘输入', value: 'type' },
            { label: '直接赋值', value: 'assign' }
          ]}
          value={draft.fillMode === 'js' ? 'assign' : draft.fillMode === 'type' ? 'type' : 'fill'}
        />
      )}
    </>
  );
}

function shouldShowSelectorControls(actionType: string): boolean {
  return (
    actionType === 'browser.fetch' ||
    actionType === 'browser.click' ||
    actionType === 'browser.hover' ||
    actionType === 'browser.fill' ||
    actionType === 'browser.wait' ||
    actionType === 'browser.extract' ||
    actionType === 'browser.dismiss' ||
    actionType === 'browser.clickLoadMore' ||
    actionType === 'browser.paginateNext' ||
    actionType === 'browser.screenshot' ||
    actionType === 'browser.select' ||
    actionType === 'browser.check' ||
    actionType === 'browser.drag' ||
    actionType === 'ui.click' ||
    actionType === 'ui.fill' ||
    actionType === 'ui.wait' ||
    actionType === 'ui.extract' ||
    actionType === 'ui.screenshot' ||
    actionType === 'ui.select' ||
    actionType === 'ui.check' ||
    actionType === 'ui.drag'
  );
}

function SelectorField({
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
          className={`h-6 px-2 ${picking ? 'text-amber-600' : 'text-blue-600'}`}
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
        <p className="mt-0.5 truncate font-mono text-[10px] text-slate-400" title={effectiveUrl}>
          {effectiveUrl}
        </p>
      )}
    </div>
  );
}

function InlineHint({ text, tone = 'default' }: { text: string; tone?: 'default' | 'warn' }): ReactElement {
  return (
    <div className={`rounded-md border px-2 py-1.5 text-[10px] leading-4 ${tone === 'warn' ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-slate-200 bg-slate-50 text-slate-500'}`}>
      {text}
    </div>
  );
}

function readBrowserHintTone(actionType: string, draft: ActionFieldsProps['draft']): 'default' | 'warn' {
  if (actionType === 'browser.fetch' && draft.targetUrl.trim() === '') {
    return 'warn';
  }
  if ((actionType === 'browser.fill' || actionType === 'ui.fill') && draft.inputValue.trim() === '') {
    return 'warn';
  }
  if (draft.selector.trim() === '') {
    return 'warn';
  }
  return 'default';
}

function readBrowserHintText(actionType: string, draft: ActionFieldsProps['draft']): string {
  if (actionType === 'browser.fetch' && draft.targetUrl.trim() === '') {
    return '建议先填写目标网址，再使用拾取器或稳定性分析生成选择器。';
  }
  if (draft.selector.trim() === '') {
    return '缺少选择器时节点无法稳定执行，优先使用拾取器或站点分析候选。';
  }
  if ((actionType === 'browser.fill' || actionType === 'ui.fill') && draft.inputValue.trim() === '') {
    return '输入类节点建议绑定变量或直接填写输入内容，否则运行时会被校验拦截。';
  }
  return actionType === 'browser.fetch'
    ? '当前节点会以这里的目标网址与选择器作为抓取入口。'
    : '选择器将直接决定当前操作组件的执行目标。';
}

function SiteAnalysisSummary({ analysis, onSelect }: { analysis: NonNullable<ElectronBridgeState['siteAnalysis']>; onSelect: (selector: string) => void }): ReactElement {
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
