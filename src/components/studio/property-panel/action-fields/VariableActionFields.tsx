import type { ReactElement } from 'react';

import type { RunLogLevel, VariableScope } from '../../../../types/rpa';
import { Field } from '../../../ui/FormControls';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../ui/select';
import { VariableNameField } from '../VariableNameField';
import { VariablePickerField } from '../VariablePickerField';
import { LabelLike } from './FieldLayout';
import type { ActionFieldsProps } from './types';

const variableScopes: VariableScope[] = ['全局', '循环', '局部'];
const logLevels: RunLogLevel[] = ['info', 'success', 'running', 'warn', 'error'];

export function VariableActionFields({ draft, electron, node, onDraftPatch }: ActionFieldsProps): ReactElement {
  const actionType = node.data.action?.type ?? `${node.data.kind}.step`;
  const availableVariables = electron.variableViews;

  if (actionType === 'variable.get') {
    return (
      <>
        <VariableNameField label="变量名" onChange={(value) => onDraftPatch('variableName', value)} placeholder="result_status" value={draft.variableName} variables={availableVariables} />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="status_value" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }

  if (actionType === 'variable.input') {
    return (
      <>
        <Field label="弹窗提示" onChange={(e) => onDraftPatch('message', e.target.value)} placeholder="请输入提示文字" value={draft.message} />
        <VariablePickerField label="默认值" onChange={(value) => onDraftPatch('defaultValue', value)} value={draft.defaultValue} variables={availableVariables} />
        <VariableNameField label="保存到变量" mode="target" onChange={(value) => onDraftPatch('variableName', value)} placeholder="user_input" value={draft.variableName} variables={availableVariables} />
        <ScopeSelect draft={draft} onDraftPatch={onDraftPatch} />
      </>
    );
  }

  if (actionType === 'variable.log') {
    return (
      <>
        <VariablePickerField label="日志内容" onChange={(value) => onDraftPatch('message', value)} value={draft.message} variables={availableVariables} />
        <LabelLike text="日志级别">
          <Select onValueChange={(value) => onDraftPatch('logLevel', value as RunLogLevel)} value={draft.logLevel}>
            <SelectTrigger className="font-mono text-[11px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {logLevels.map((level) => (
                <SelectItem key={level} value={level}>
                  {level}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </LabelLike>
      </>
    );
  }

  if (actionType === 'variable.notify') {
    return (
      <>
        <Field label="通知通道" onChange={(event) => onDraftPatch('channel', event.target.value)} placeholder="企业微信" value={draft.channel} />
        <VariablePickerField label="通知内容" onChange={(value) => onDraftPatch('message', value)} value={draft.message} variables={availableVariables} />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="notification_message" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }

  if (actionType === 'variable.clipboard') {
    return (
      <>
        <VariablePickerField label="剪贴板内容" onChange={(value) => onDraftPatch('content', value)} value={draft.content} variables={availableVariables} />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="clipboard_text" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }

  // 隐式默认分支：兜底为 variable.set，新增 variable.* 动作类型需在此之前显式处理
  return (
    <>
      <VariableNameField label="变量名" mode="target" onChange={(value) => onDraftPatch('variableName', value)} placeholder="result_status" value={draft.variableName} variables={availableVariables} />
      <VariablePickerField label="变量值" onChange={(value) => onDraftPatch('defaultValue', value)} value={draft.defaultValue} variables={availableVariables} />
      <ScopeSelect draft={draft} onDraftPatch={onDraftPatch} />
      <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="result_status" value={draft.responseVariable} variables={availableVariables} />
    </>
  );
}

function ScopeSelect({ draft, onDraftPatch }: Pick<ActionFieldsProps, 'draft' | 'onDraftPatch'>): ReactElement {
  return (
    <LabelLike text="变量作用域">
      <Select onValueChange={(value) => onDraftPatch('variableScope', value as VariableScope)} value={draft.variableScope}>
        <SelectTrigger className="font-mono text-[11px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {variableScopes.map((scope) => (
            <SelectItem key={scope} value={scope}>
              {scope}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </LabelLike>
  );
}
