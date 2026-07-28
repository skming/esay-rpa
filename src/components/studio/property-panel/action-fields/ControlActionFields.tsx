import type { ReactElement } from 'react';

import { Field } from '../../../ui/FormControls';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../ui/select';
import { VariableNameField } from '../VariableNameField';
import { VariablePickerField } from '../VariablePickerField';
import { LabelLike } from './FieldLayout';
import type { ActionFieldsProps } from './types';
import { DEFAULT_ACTION_TYPE_BY_KIND } from '../../../../types/rpa';

export function ControlActionFields({ draft, electron, node, onDraftPatch }: Pick<ActionFieldsProps, 'draft' | 'electron' | 'node' | 'onDraftPatch'>): ReactElement {
  const actionType = node.data.action?.type ?? DEFAULT_ACTION_TYPE_BY_KIND[node.data.kind];
  const availableVariables = electron.variableViews;
  if (actionType === 'control.condition') {
    return <VariablePickerField label="条件表达式" onChange={(value) => onDraftPatch('inputValue', value)} value={draft.inputValue} variables={availableVariables} />;
  }
  if (actionType === 'control.delay') {
    return (
      <>
        <Field label="延时毫秒" onChange={(event) => onDraftPatch('delayMs', Math.max(0, Number.parseInt(event.target.value, 10) || 0))} type="number" value={String(draft.delayMs)} />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="delay_ms" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }
  if (actionType === 'control.break') {
    return <Field label="中断说明" onChange={(event) => onDraftPatch('description', event.target.value)} placeholder="跳出当前循环" value={draft.description} />;
  }
  if (actionType === 'control.noop') {
    return <Field label="控制说明" onChange={(event) => onDraftPatch('description', event.target.value)} placeholder="用于流程占位或分组说明" value={draft.description} />;
  }
  if (actionType === 'control.retry') {
    return (
      <>
        <Field
          label="最大重试次数"
          onChange={(event) => onDraftPatch('retryCount', Math.max(1, Number.parseInt(event.target.value, 10) || 1))}
          type="number"
          value={String(draft.retryCount)}
        />
        <Field
          label="重试间隔 (ms)"
          onChange={(event) => onDraftPatch('delayMs', Math.max(0, Number.parseInt(event.target.value, 10) || 0))}
          type="number"
          value={String(draft.delayMs)}
        />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="retry_count" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }
  if (actionType === 'control.try') {
    return (
      <>
        <VariableNameField label="异常变量" mode="target" onChange={(value) => onDraftPatch('errorVariable', value)} placeholder="caught_error" value={draft.errorVariable} variables={availableVariables} />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="try_result" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }
  if (actionType === 'control.subprocess') {
    return (
      <>
        <Field label="子流程 ID" mono onChange={(event) => onDraftPatch('flowId', event.target.value)} placeholder="flow-abc123" value={draft.flowId} />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="subprocess_result" value={draft.responseVariable} variables={availableVariables} />
        <VariableNameField label="状态变量" mode="target" onChange={(value) => onDraftPatch('statusVariable', value)} placeholder="subprocess_status" value={draft.statusVariable} variables={availableVariables} />
      </>
    );
  }
  if (actionType === 'control.human_takeover') {
    return (
      <>
        <Field label="提示信息" onChange={(event) => onDraftPatch('humanTakeoverMessage', event.target.value)} placeholder="请完成验证后点击继续" value={draft.humanTakeoverMessage} />
        <LabelLike text="默认恢复方式">
          <Select onValueChange={(value) => onDraftPatch('humanTakeoverResumeMode', value as 'next_node' | 'current_node')} value={draft.humanTakeoverResumeMode}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="next_node">已完成，继续下一节点</SelectItem>
              <SelectItem value="current_node">重试当前节点</SelectItem>
            </SelectContent>
          </Select>
        </LabelLike>
      </>
    );
  }
  if (actionType === 'control.repeat_until') {
    return (
      <>
        <VariablePickerField label="退出条件" onChange={(value) => onDraftPatch('inputValue', value)} value={draft.inputValue} variables={availableVariables} />
        <VariableNameField label="轮数变量" mode="target" onChange={(value) => onDraftPatch('indexVariable', value)} placeholder="repeat_index" value={draft.indexVariable} variables={availableVariables} />
        <Field
          label="最大轮数"
          onChange={(event) => onDraftPatch('maxIterations', Math.max(1, Number.parseInt(event.target.value, 10) || 1))}
          type="number"
          value={String(draft.maxIterations)}
        />
      </>
    );
  }
  // 隐式默认分支：未匹配上述 control.* 类型时按 control.foreach 渲染，新增 control 动作类型需在此之前显式处理
  return (
    <>
      <VariableNameField label="遍历变量" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="excel_rows" value={draft.responseVariable} variables={availableVariables} />
      <VariableNameField label="当前项变量" mode="target" onChange={(value) => onDraftPatch('itemVariable', value)} placeholder="current_row" value={draft.itemVariable} variables={availableVariables} />
      <VariableNameField label="索引变量" mode="target" onChange={(value) => onDraftPatch('indexVariable', value)} placeholder="loop_index" value={draft.indexVariable} variables={availableVariables} />
      <Field
        label="最大迭代次数"
        onChange={(event) => onDraftPatch('maxIterations', Math.max(1, Number.parseInt(event.target.value, 10) || 1))}
        type="number"
        value={String(draft.maxIterations)}
      />
    </>
  );
}
