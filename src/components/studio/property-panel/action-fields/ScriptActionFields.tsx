import type { ReactElement } from 'react';

import { CodeEditor } from '../../../ui/CodeBlock';
import { Field } from '../../../ui/FormControls';
import { VariableNameField } from '../VariableNameField';
import type { ActionFieldsProps } from './types';

export function ScriptActionFields({ draft, electron, node, onDraftPatch }: Pick<ActionFieldsProps, 'draft' | 'electron' | 'node' | 'onDraftPatch'>): ReactElement {
  const actionType = node.data.action?.type ?? 'script.python';
  const availableVariables = electron.variableViews;

  if (actionType === 'script.shell') {
    return (
      <>
        <Field
          label="Shell 命令"
          mono
          onChange={(event) => onDraftPatch('command', event.target.value)}
          placeholder="echo ${var.input}"
          value={draft.command}
        />
        <VariableNameField label="标准输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="shell_output" value={draft.responseVariable} variables={availableVariables} />
        <VariableNameField label="退出码变量" mode="target" onChange={(value) => onDraftPatch('statusVariable', value)} placeholder="shell_exit_code" value={draft.statusVariable} variables={availableVariables} />
        <VariableNameField label="错误输出变量" mode="target" onChange={(value) => onDraftPatch('stderrVariable', value)} placeholder="shell_stderr" value={draft.stderrVariable} variables={availableVariables} />
      </>
    );
  }

  if (actionType === 'script.websocket') {
    return (
      <>
        <Field
          label="WebSocket 地址"
          mono
          onChange={(event) => onDraftPatch('targetUrl', event.target.value)}
          placeholder="ws://localhost:8080"
          value={draft.targetUrl}
        />
        <Field
          label="发送消息"
          mono
          onChange={(event) => onDraftPatch('message', event.target.value)}
          placeholder="${var.ws_message}"
          value={draft.message}
        />
        <VariableNameField label="接收消息变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="ws_response" value={draft.responseVariable} variables={availableVariables} />
        <VariableNameField label="状态变量" mode="target" onChange={(value) => onDraftPatch('statusVariable', value)} placeholder="ws_status" value={draft.statusVariable} variables={availableVariables} />
      </>
    );
  }

  const expectJavascript = actionType === 'script.javascript';
  // Inline code mode: node was created with a `code` field (e.g. by AI assistant)
  const isCodeMode = draft.code.trim() !== '';

  return (
    <>
      {isCodeMode ? (
        <>
          <CodeEditor
            height={200}
            label={expectJavascript ? '内联 JS 代码' : '内联 Python 代码'}
            language={expectJavascript ? 'javascript' : 'python'}
            onChange={(value) => onDraftPatch('code', value)}
            placeholder={expectJavascript ? 'console.log("hello")' : 'print("hello")'}
            value={draft.code}
          />
          <div className="rounded-md border border-blue-200 bg-blue-50 px-2 py-1.5 text-[10px] leading-4 text-blue-700">
            内联代码模式 — 代码直接嵌入节点，无需脚本文件。清空代码内容可切换到文件路径模式。
          </div>
        </>
      ) : (
        <>
          <Field
            label={expectJavascript ? 'JS 脚本路径' : 'Python 脚本路径'}
            mono
            onChange={(event) => onDraftPatch('path', event.target.value)}
            placeholder={expectJavascript ? 'scripts/transform.js' : 'scripts/data_clean.py'}
            value={draft.path}
          />
          {readScriptHintTone(draft.path, expectJavascript) === 'warn' ? (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-[10px] leading-4 text-amber-800">
              {readScriptHintText(draft.path, expectJavascript)}
            </div>
          ) : (
            <div className="rounded-md border border-slate-200 px-2 py-1.5 text-[10px] leading-4 text-slate-900">
              {readScriptHintText(draft.path, expectJavascript)}
            </div>
          )}
        </>
      )}
      <VariableNameField label="标准输出变量" mode="target" onChange={(value) => onDraftPatch('responseVariable', value)} placeholder="script_stdout" value={draft.responseVariable} variables={availableVariables} />
      <VariableNameField label="退出码变量" mode="target" onChange={(value) => onDraftPatch('statusVariable', value)} placeholder="script_exit_code" value={draft.statusVariable} variables={availableVariables} />
      <VariableNameField label="错误输出变量" mode="target" onChange={(value) => onDraftPatch('stderrVariable', value)} placeholder="script_stderr" value={draft.stderrVariable} variables={availableVariables} />
    </>
  );
}

function readScriptHintTone(path: string, expectJavascript: boolean): 'default' | 'warn' {
  if (path.trim() === '') {
    return 'warn';
  }
  if (expectJavascript && !path.trim().endsWith('.js')) {
    return 'warn';
  }
  if (!expectJavascript && !path.trim().endsWith('.py')) {
    return 'warn';
  }
  return 'default';
}

function readScriptHintText(path: string, expectJavascript: boolean): string {
  if (path.trim() === '') {
    return '请指定脚本文件路径，或由 RPA 助手生成内联代码节点（自动切换代码模式）。';
  }
  if (expectJavascript && !path.trim().endsWith('.js')) {
    return 'JavaScript 节点建议使用 .js 脚本，避免运行时解释器与扩展名不匹配。';
  }
  if (!expectJavascript && !path.trim().endsWith('.py')) {
    return 'Python 节点建议使用 .py 脚本，避免运行时解释器与扩展名不匹配。';
  }
  return '建议同时配置标准输出、退出码、错误输出变量，方便运行后排障。';
}
