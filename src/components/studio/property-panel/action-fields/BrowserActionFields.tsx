import { SearchCheck } from 'lucide-react';
import type { ReactElement } from 'react';

import { Button } from '../../../ui/button';
import { CodeEditor } from '../../../ui/CodeBlock';
import { Field, Segmented, TextareaField } from '../../../ui/FormControls';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../ui/select';
import { LabelLike } from './FieldLayout';
import { VariableNameField } from '../VariableNameField';
import { VariablePickerField } from '../VariablePickerField';
import { CheckedStateField, ExtractModeField, InlineHint, SelectorField, SiteAnalysisSummary } from './BrowserFieldParts';
import { readBrowserHintText, readBrowserHintTone } from './browserHints';
import type { ActionFieldsProps } from './types';
import { DEFAULT_ACTION_TYPE_BY_KIND } from '../../../../types/rpa';

// 需要选择器输入框的动作；不在表里的（如 browser.scroll、browser.tab.*）不显示拾取器区块
const SELECTOR_ACTION_TYPES = new Set([
  'browser.fetch', 'browser.click', 'browser.hover', 'browser.fill', 'browser.wait', 'browser.waitFor',
  'browser.extract', 'browser.dismiss', 'browser.clickLoadMore', 'browser.paginateNext', 'browser.screenshot',
  'browser.select', 'browser.check', 'browser.drag',
  'ui.click', 'ui.fill', 'ui.wait', 'ui.extract', 'ui.screenshot', 'ui.select', 'ui.check', 'ui.drag'
]);
// 定位到单个元素才谈得上"备选选择器/文字锚点"自愈；抓取类与拖拽类不适用
const RESILIENCE_EXCLUDED_TYPES = new Set(['browser.dismiss', 'browser.screenshot', 'browser.drag', 'browser.fetch', 'ui.drag']);

export function BrowserActionFields({ draft, electron, flowTargetUrl, node, onDraftPatch }: ActionFieldsProps): ReactElement {
  const actionType = node.data.action?.type ?? DEFAULT_ACTION_TYPE_BY_KIND[node.data.kind];
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
        <ExtractModeField onChange={(value) => onDraftPatch('extractMode', value)} value={draft.extractMode} />
        {draft.extractMode === 'attribute' &&<Field label="属性名" mono onChange={(event) => onDraftPatch('attribute', event.target.value)} placeholder="href" value={draft.attribute} />}
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
        <CheckedStateField onChange={(value) => onDraftPatch('checked', value)} value={draft.checked} />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="check_result" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }

  if (actionType === 'browser.ensureLogin') {
    return (
      <>
        <Field
          label="目标网址"
          mono
          onChange={(event) => onDraftPatch('targetUrl', event.target.value)}
          placeholder="https://example.com/"
          value={draft.targetUrl}
        />
        <SelectorField electron={electron} label="已登录特征 (CSS)" onChange={(value) => onDraftPatch('selector', value)} targetUrl={resolvedTargetUrl} value={draft.selector} />
        <Field
          label="未登录特征 (CSS)"
          mono
          onChange={(event) => onDraftPatch('targetSelector', event.target.value)}
          placeholder="input[type='password']"
          value={draft.targetSelector}
        />
        <VariableNameField label="登录态输出变量" mode="target" onChange={(value) => onDraftPatch('statusVariable', value)} placeholder="login_status" value={draft.statusVariable} variables={availableVariables} />
      </>
    );
  }

  const selectorLabel = node.data.kind === 'ui' ? '目标控件 (CSS)' : '目标元素 (CSS)';
  const usesSelector = SELECTOR_ACTION_TYPES.has(actionType);
  const isExtract = actionType === 'browser.extract' || actionType === 'ui.extract';
  const hasExtractMode = isExtract || actionType === 'browser.fetch';
  const isWaitFor = actionType === 'browser.waitFor';
  const isFill = actionType === 'browser.fill' || actionType === 'ui.fill';
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
      {(actionType === 'browser.fetch' || isFill) && (
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
      {usesSelector && !RESILIENCE_EXCLUDED_TYPES.has(actionType) && (
        <>
          <TextareaField
            hint="每行一个，主选择器未命中时自动尝试"
            label="备选选择器"
            mono
            onChange={(event) => onDraftPatch('fallbackSelectors', event.target.value)}
            placeholder={'#login-btn\nbutton.submit'}
            rows={2}
            value={draft.fallbackSelectors}
          />
          <Field
            hint="按元素可见文字兜底定位"
            label="文字锚点"
            onChange={(event) => onDraftPatch('anchorText', event.target.value)}
            placeholder="登录"
            value={draft.anchorText}
          />
        </>
      )}
      {hasExtractMode && <ExtractModeField onChange={(value) => onDraftPatch('extractMode', value)} value={draft.extractMode} />}
      {hasExtractMode && draft.extractMode === 'attribute' && (
        <Field label="属性名" mono onChange={(event) => onDraftPatch('attribute', event.target.value)} placeholder="href" value={draft.attribute} />
      )}
      {(isExtract || actionType === 'browser.clickLoadMore' || actionType === 'browser.paginateNext') && (
        <div>
          {/* outputSchema 需与后端提取协议对齐：JSON 数组，每项 {name, aliases?, required?}，required 字段未命中会中断执行 */}
          <span className="mb-1 block text-[11px] font-medium text-slate-600">输出字段 Schema</span>
          <CodeEditor
            language="json"
            minHeight={56}
            onChange={(value) => onDraftPatch('outputSchema', value)}
            placeholder={'[{"name":"品名","aliases":["名称"]},{"name":"价格"}]'}
            value={draft.outputSchema}
          />
          <span className="mt-1 block text-[10px] leading-4 font-normal text-slate-500">JSON 数组，声明期望字段；必需字段未命中时报错</span>
        </div>
      )}
      {isWaitFor && (
        <LabelLike text="等待条件">
          <Select onValueChange={(value) => onDraftPatch('waitCondition', value as 'visible' | 'hidden' | 'textContains')} value={draft.waitCondition}>
            <SelectTrigger className="font-mono text-[11px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="visible">出现并可见</SelectItem>
              <SelectItem value="hidden">消失/不可见</SelectItem>
              <SelectItem value="textContains">文本包含指定内容</SelectItem>
            </SelectContent>
          </Select>
        </LabelLike>
      )}
      {isWaitFor && draft.waitCondition === 'textContains' && (
        <Field label="期望包含的文本" onChange={(event) => onDraftPatch('inputValue', event.target.value)} placeholder="加载完成" value={draft.inputValue} />
      )}
      {(isFill || actionType === 'ui.select') && <VariablePickerField onChange={(value) => onDraftPatch('inputValue', value)} value={draft.inputValue} variables={availableVariables} />}
      {(isExtract || actionType === 'ui.screenshot' || actionType === 'ui.select' || actionType === 'ui.check' || actionType === 'ui.drag') && (
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="ui_values" value={draft.responseVariable} variables={availableVariables} />
      )}
      {isExtract && (
        <>
          <VariableNameField label="首值变量" mode="target" onChange={(value) => onDraftPatch('firstValueVariable', value)} placeholder="first_text" value={draft.firstValueVariable} variables={availableVariables} />
          <VariableNameField label="命中数量变量" mode="target" onChange={(value) => onDraftPatch('statusVariable', value)} placeholder="match_count" value={draft.statusVariable} variables={availableVariables} />
        </>
      )}
      {actionType === 'ui.drag' && <Field label="目标控件" mono onChange={(event) => onDraftPatch('targetSelector', event.target.value)} placeholder="#target" value={draft.targetSelector} />}
      {actionType === 'ui.check' && <CheckedStateField onChange={(value) => onDraftPatch('checked', value)} value={draft.checked} />}
      {isFill && (
        // UI 选项"直接赋值"落到后端 fillMode 值 'js'（走 JS 赋值执行路径），与展示文案不同名
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

