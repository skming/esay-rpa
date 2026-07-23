import type { ReactElement } from 'react';
import { useState } from 'react';

import { Eye } from 'lucide-react';
import { Button } from '../../../ui/button';
import { Field } from '../../../ui/FormControls';
import { VariableNameField } from '../VariableNameField';
import { VariablePickerField } from '../VariablePickerField';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../ui/select';
import { LabelLike } from './FieldLayout';
import { ExcelPreviewDialog } from '../ExcelPreviewDialog';
import type { ActionFieldsProps } from './types';

export function FileActionFields({ draft, electron, node, onDraftPatch }: Pick<ActionFieldsProps, 'draft' | 'electron' | 'node' | 'onDraftPatch'>): ReactElement {
  const actionType = node.data.action?.type ?? `${node.data.kind}.step`;
  const availableVariables = electron.variableViews;
  const [previewOpen, setPreviewOpen] = useState(false);

  if (actionType === 'file.compress') {
    return (
      <>
        <Field label="源路径" mono onChange={(event) => onDraftPatch('path', event.target.value)} placeholder="data/input/" value={draft.path} />
        <Field label="输出路径" mono onChange={(event) => onDraftPatch('targetPath', event.target.value)} placeholder="archives/output.zip" value={draft.targetPath} />
        <LabelLike text="操作方式">
          <Select onValueChange={(value) => onDraftPatch('operation', value)} value={draft.operation}>
            <SelectTrigger className="font-mono text-[11px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="compress">压缩</SelectItem>
              <SelectItem value="decompress">解压</SelectItem>
            </SelectContent>
          </Select>
        </LabelLike>
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="archive_path" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }

  if (actionType === 'file.rename') {
    return (
      <>
        <Field label="源路径" mono onChange={(event) => onDraftPatch('path', event.target.value)} placeholder="data/old_name.txt" value={draft.path} />
        <Field label="新路径" mono onChange={(event) => onDraftPatch('targetPath', event.target.value)} placeholder="data/new_name.txt" value={draft.targetPath} />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="renamed_path" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }

  if (actionType === 'file.watch') {
    return (
      <>
        <Field label="监听目录" mono onChange={(event) => onDraftPatch('path', event.target.value)} placeholder="data/" value={draft.path} />
        <Field label="匹配规则" mono onChange={(event) => onDraftPatch('pattern', event.target.value)} placeholder="*.csv" value={draft.pattern} />
        <VariableNameField label="变更文件变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="changed_files" value={draft.responseVariable} variables={availableVariables} />
        <VariableNameField label="变更数量变量" mode="target" onChange={(value) => onDraftPatch('statusVariable', value)} placeholder="changed_count" value={draft.statusVariable} variables={availableVariables} />
      </>
    );
  }

  if (actionType === 'excel.addrow') {
    return (
      <>
        <Field label="CSV 路径" mono onChange={(event) => onDraftPatch('path', event.target.value)} placeholder="data/orders.csv" value={draft.path} />
        <VariablePickerField label="新增行数据 (JSON)" onChange={(value) => onDraftPatch('content', value)} value={draft.content} variables={availableVariables} />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="excel_row_count" value={draft.responseVariable} variables={availableVariables} />
        <VariableNameField label="计数变量" mode="target" onChange={(value) => onDraftPatch('statusVariable', value)} placeholder="excel_row_count" value={draft.statusVariable} variables={availableVariables} />
      </>
    );
  }

  if (actionType === 'excel.deleterow') {
    return (
      <>
        <Field label="CSV 路径" mono onChange={(event) => onDraftPatch('path', event.target.value)} placeholder="data/orders.csv" value={draft.path} />
        <Field label="行索引 (0-based)" onChange={(event) => onDraftPatch('tabIndex', Math.max(0, Number.parseInt(event.target.value, 10) || 0))} type="number" value={String(draft.tabIndex)} />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="excel_row_count" value={draft.responseVariable} variables={availableVariables} />
        <VariableNameField label="计数变量" mode="target" onChange={(value) => onDraftPatch('statusVariable', value)} placeholder="excel_row_count" value={draft.statusVariable} variables={availableVariables} />
      </>
    );
  }

  if (actionType === 'excel.save') {
    return (
      <>
        <Field label="CSV 路径" mono onChange={(event) => onDraftPatch('path', event.target.value)} placeholder="${var.output_prefix}.csv" value={draft.path} />
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="excel_save_path" value={draft.responseVariable} variables={availableVariables} />
      </>
    );
  }

  if (actionType === 'excel.filter') {
    return (
      <>
        <Field label="CSV 路径" mono onChange={(event) => onDraftPatch('path', event.target.value)} placeholder="data/orders.csv" value={draft.path} />
        <Field label="列名" mono onChange={(event) => onDraftPatch('column', event.target.value)} placeholder="status" value={draft.column} />
        <LabelLike text="操作方式">
          <Select onValueChange={(value) => onDraftPatch('operation', value)} value={draft.operation}>
            <SelectTrigger className="font-mono text-[11px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="filter">过滤</SelectItem>
              <SelectItem value="sort_asc">升序排序</SelectItem>
              <SelectItem value="sort_desc">降序排序</SelectItem>
              <SelectItem value="group">分组</SelectItem>
            </SelectContent>
          </Select>
        </LabelLike>
        {draft.operation === 'filter' && <Field label="过滤条件" mono onChange={(event) => onDraftPatch('pattern', event.target.value)} placeholder="done" value={draft.pattern} />}
        <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="filtered_rows" value={draft.responseVariable} variables={availableVariables} />
        <VariableNameField label="计数变量" mode="target" onChange={(value) => onDraftPatch('statusVariable', value)} placeholder="filtered_count" value={draft.statusVariable} variables={availableVariables} />
      </>
    );
  }

  const isWrite = actionType.endsWith('.write');
  const isExcel = actionType.startsWith('excel.');
  const isCopyMove = actionType === 'file.copy' || actionType === 'file.move';
  const isList = actionType === 'file.list';
  const isDelete = actionType === 'file.delete';

  return (
    <>
      <Field
        label={isExcel ? 'CSV 路径' : isList ? '目录路径' : isCopyMove ? '源文件路径' : '文件路径'}
        mono
        onChange={(event) => onDraftPatch('path', event.target.value)}
        placeholder={isExcel ? 'data/orders.csv' : isList ? 'data' : isDelete ? 'temp/remove.txt' : isWrite ? '${var.output_prefix}.txt' : 'data/input.txt'}
        value={draft.path}
      />
      {isCopyMove && <Field label="目标路径" mono onChange={(event) => onDraftPatch('targetPath', event.target.value)} placeholder="archive/input.txt" value={draft.targetPath} />}
      {isExcel && draft.path.trim() !== '' && (
        <Button
          className="w-full justify-start gap-1.5 text-[11px]"
          onClick={() => setPreviewOpen(true)}
          size="sm"
          variant="outline"
        >
          <Eye className="h-3.5 w-3.5 text-emerald-600" strokeWidth={1.5} />
          预览 CSV 内容
        </Button>
      )}
      {isExcel && (
        <ExcelPreviewDialog
          onOpenChange={setPreviewOpen}
          open={previewOpen}
          path={draft.path}
        />
      )}
      <div className={`rounded-md border px-2 py-1.5 text-[10px] leading-4 ${readFileHintTone({ draft, isCopyMove }) === 'warn' ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-slate-200 bg-slate-50 text-slate-500'}`}>
        {readFileHintText({ draft, isCopyMove, isDelete, isExcel, isList, isWrite })}
      </div>
      {isList && <Field label="匹配规则" mono onChange={(event) => onDraftPatch('pattern', event.target.value)} placeholder="*.txt" value={draft.pattern} />}
      {isExcel && !isWrite && <Field label="读取列名" mono onChange={(event) => onDraftPatch('column', event.target.value)} placeholder="order_id" value={draft.column} />}
      {isWrite && <VariablePickerField label={isExcel ? '写入内容' : '文件内容'} onChange={(value) => onDraftPatch('content', value)} value={draft.content} variables={availableVariables} />}
      <VariableNameField label="输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder={isWrite ? 'output_path' : 'rows'} value={draft.responseVariable} variables={availableVariables} />
      <VariableNameField label="计数变量" mode="target" onChange={(value) => onDraftPatch('statusVariable', value)} placeholder="row_count" value={draft.statusVariable} variables={availableVariables} />
    </>
  );
}

function readFileHintTone({
  draft,
  isCopyMove
}: {
  draft: ActionFieldsProps['draft'];
  isCopyMove: boolean;
}): 'default' | 'warn' {
  if (draft.path.trim() === '') {
    return 'warn';
  }
  if (isCopyMove && draft.targetPath.trim() === '') {
    return 'warn';
  }
  return 'default';
}

function readFileHintText({
  draft,
  isCopyMove,
  isDelete,
  isExcel,
  isList,
  isWrite
}: {
  draft: ActionFieldsProps['draft'];
  isCopyMove: boolean;
  isDelete: boolean;
  isExcel: boolean;
  isList: boolean;
  isWrite: boolean;
}): string {
  if (draft.path.trim() === '') {
    return '请先填写源路径，路径类节点缺少 path 时无法执行。';
  }
  if (isCopyMove && draft.targetPath.trim() === '') {
    return '复制/移动节点必须同时具备源路径和目标路径。';
  }
  if (isList) {
    return '目录遍历建议同时设置匹配规则与输出变量，避免把无关文件带入后续步骤。';
  }
  if (isExcel && !isWrite) {
    return '读取 CSV 时建议明确列名，并配置计数变量方便后续循环控制。';
  }
  if (isDelete) {
    return '删除节点是不可逆操作，建议先在上游写入备份路径或开启截图/日志记录。';
  }
  if (isWrite) {
    return '写入类节点建议把输出路径写入变量，便于后续归档或通知步骤复用。';
  }
  return '文件节点建议显式配置输出变量，避免后续步骤依赖隐式路径。';
}
