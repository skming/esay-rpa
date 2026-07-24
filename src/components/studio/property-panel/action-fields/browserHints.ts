import type { ActionFieldsProps } from './types';

/** browser / ui 字段区顶部提示条的文案与色调，只依赖草稿值，不含 JSX。 */

export function readBrowserHintTone(actionType: string, draft: ActionFieldsProps['draft']): 'default' | 'warn' {
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

export function readBrowserHintText(actionType: string, draft: ActionFieldsProps['draft']): string {
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
