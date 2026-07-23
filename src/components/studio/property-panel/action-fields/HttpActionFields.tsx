import type { ReactElement } from 'react';

import type { RpaNodeConfigDraft } from '../../../../types/rpa';
import { Field } from '../../../ui/FormControls';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../ui/select';
import { VariableNameField } from '../VariableNameField';
import { VariablePickerField } from '../VariablePickerField';
import { LabelLike } from './FieldLayout';
import type { ActionFieldsProps } from './types';

export function HttpActionFields({ draft, electron, onDraftPatch }: Pick<ActionFieldsProps, 'draft' | 'electron' | 'onDraftPatch'>): ReactElement {
  const availableVariables = electron.variableViews;
  const showRequestBody = draft.method !== 'GET';
  return (
    <>
      <div className="grid grid-cols-[88px_1fr] gap-2">
        <LabelLike text="请求方法">
          <Select onValueChange={(value) => onDraftPatch('method', value as RpaNodeConfigDraft['method'])} value={draft.method}>
            <SelectTrigger className="font-mono text-[11px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const).map((method) => (
                <SelectItem key={method} value={method}>
                  {method}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </LabelLike>
        <Field label="请求 URL" mono onChange={(event) => onDraftPatch('targetUrl', event.target.value)} placeholder="https://api.example.com/data" value={draft.targetUrl} />
      </div>
      <div className={`rounded-md border px-2 py-1.5 text-[10px] leading-4 ${draft.targetUrl.trim() === '' ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-slate-200 bg-slate-50 text-slate-500'}`}>
        {draft.targetUrl.trim() === ''
          ? 'HTTP 节点至少需要请求 URL；若使用模板变量，确保运行前变量已定义。'
          : showRequestBody
            ? 'POST / PUT / PATCH 请求建议通过变量模板构造请求体，避免写死动态参数。'
            : 'GET 请求通常不需要请求体；如需参数，优先放在 URL 查询串中。'}
      </div>
      {showRequestBody && <VariablePickerField label="请求体" onChange={(value) => onDraftPatch('requestBody', value)} value={draft.requestBody} variables={availableVariables} />}
      <VariableNameField label="响应变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="http_response" value={draft.responseVariable} variables={availableVariables} />
      <VariableNameField label="状态码变量" mode="target" onChange={(value) => onDraftPatch('statusVariable', value)} placeholder="http_status" value={draft.statusVariable} variables={availableVariables} />
    </>
  );
}
