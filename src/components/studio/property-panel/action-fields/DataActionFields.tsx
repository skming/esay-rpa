import type { ReactElement } from 'react';

import { Field } from '../../../ui/FormControls';
import { VariableNameField } from '../VariableNameField';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../ui/select';
import { LabelLike } from './FieldLayout';
import type { ActionFieldsProps } from './types';

export function DataActionFields({ draft, electron, node, onDraftPatch }: Pick<ActionFieldsProps, 'draft' | 'electron' | 'node' | 'onDraftPatch'>): ReactElement {
  const actionType = node.data.action?.type ?? 'data.string.transform';
  const availableVariables = electron.variableViews;
  if (actionType === 'data.convert') {
    return (
      <>
        <Field label="输入值" mono onChange={(event) => onDraftPatch('inputValue', event.target.value)} placeholder="${var.input_text}" value={draft.inputValue} />
        <LabelLike text="转换方式">
          <Select onValueChange={(value) => onDraftPatch('operation', value)} value={draft.operation}>
            <SelectTrigger className="font-mono text-[11px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="to_int">转整数</SelectItem>
              <SelectItem value="to_float">转浮点数</SelectItem>
              <SelectItem value="to_bool">转布尔值</SelectItem>
              <SelectItem value="to_str">转字符串</SelectItem>
              <SelectItem value="to_list">转列表</SelectItem>
              <SelectItem value="to_json">转 JSON</SelectItem>
            </SelectContent>
          </Select>
        </LabelLike>
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="converted_value" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }

  if (actionType === 'data.encrypt') {
    return (
      <>
        <Field label="输入内容" mono onChange={(event) => onDraftPatch('inputValue', event.target.value)} placeholder="${var.input_text}" value={draft.inputValue} />
        <LabelLike text="加密方式">
          <Select onValueChange={(value) => onDraftPatch('operation', value)} value={draft.operation}>
            <SelectTrigger className="font-mono text-[11px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="md5">MD5</SelectItem>
              <SelectItem value="sha256">SHA-256</SelectItem>
              <SelectItem value="sha1">SHA-1</SelectItem>
              <SelectItem value="base64_encode">Base64 编码</SelectItem>
              <SelectItem value="base64_decode">Base64 解码</SelectItem>
              <SelectItem value="aes_encrypt">AES 加密</SelectItem>
              <SelectItem value="aes_decrypt">AES 解密</SelectItem>
            </SelectContent>
          </Select>
        </LabelLike>
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="encrypted_value" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }

  if (actionType === 'data.math.compute') {
    return (
      <>
        <div className="grid grid-cols-[1fr_92px_1fr] gap-2">
          <Field label="左操作数" mono onChange={(event) => onDraftPatch('left', event.target.value)} placeholder="${var.left}" value={draft.left} />
          <LabelLike text="运算">
            <Select onValueChange={(value) => onDraftPatch('operation', value)} value={draft.operation}>
              <SelectTrigger className="font-mono text-[11px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="add">+</SelectItem>
                <SelectItem value="subtract">-</SelectItem>
                <SelectItem value="multiply">*</SelectItem>
                <SelectItem value="divide">/</SelectItem>
                <SelectItem value="mod">%</SelectItem>
              </SelectContent>
            </Select>
          </LabelLike>
          <Field label="右操作数" mono onChange={(event) => onDraftPatch('right', event.target.value)} placeholder="${var.right}" value={draft.right} />
        </div>
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="math_result" value={draft.responseVariable} variables={availableVariables} />
        <VariableNameField label="计数变量" mode="target" onChange={(value) => onDraftPatch('statusVariable', value)} placeholder="math_count" value={draft.statusVariable} variables={availableVariables} />
      </>
    );
  }

  return (
    <>
      {actionType === 'data.json.parse' || actionType === 'data.list.map' ? (
        <VariableNameField label="输入变量" onChange={(value) => onDraftPatch('inputVariable', value)} placeholder={actionType === 'data.list.map' ? 'excel_rows' : 'http_response'} value={draft.inputVariable} variables={availableVariables} />
      ) : (
        <Field label="输入值" mono onChange={(event) => onDraftPatch('inputValue', event.target.value)} placeholder="${var.input_text}" value={draft.inputValue} />
      )}
      {actionType === 'data.string.transform' && <DataOperationSelect draft={draft} onDraftPatch={onDraftPatch} operations={['trim', 'lower', 'upper', 'split', 'replace']} />}
      {actionType === 'data.list.map' && <DataOperationSelect draft={draft} onDraftPatch={onDraftPatch} operations={['compact', 'unique', 'join']} />}
      {actionType === 'data.regex.match' && <Field label="正则表达式" mono onChange={(event) => onDraftPatch('pattern', event.target.value)} placeholder="(\\d+)" value={draft.pattern} />}
      {/* 按 operation 取值判断而非 actionType：split(字符串处理)与 join(列表映射)共用同一 delimiter 字段 */}
      {(draft.operation === 'split' || draft.operation === 'join') && <Field label="分隔符" mono onChange={(event) => onDraftPatch('delimiter', event.target.value)} placeholder="," value={draft.delimiter} />}
      <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="data_output" value={draft.responseVariable} variables={availableVariables} />
      <VariableNameField label="计数变量" mode="target" onChange={(value) => onDraftPatch('statusVariable', value)} placeholder="data_count" value={draft.statusVariable} variables={availableVariables} />
    </>
  );
}

function DataOperationSelect({
  draft,
  onDraftPatch,
  operations
}: Pick<ActionFieldsProps, 'draft' | 'onDraftPatch'> & { operations: string[] }): ReactElement {
  return (
    <LabelLike text="处理方式">
      <Select onValueChange={(value) => onDraftPatch('operation', value)} value={draft.operation}>
        <SelectTrigger className="font-mono text-[11px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {operations.map((operation) => (
            <SelectItem key={operation} value={operation}>
              {operation}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </LabelLike>
  );
}
