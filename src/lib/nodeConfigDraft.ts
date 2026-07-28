import { DEFAULT_ACTION_TYPE_BY_KIND, type RpaNodeConfigDraft, type RpaNodeData } from '../types/rpa';

/** 把节点的 action（按类型才有的字段）展平成配置面板统一的草稿表单；新增 action 字段需同时更新 applyNodeConfigDraft 做反向写回。 */
export function createNodeConfigDraft(data: RpaNodeData): RpaNodeConfigDraft {
  const actionType = data.action?.type ?? DEFAULT_ACTION_TYPE_BY_KIND[data.kind];
  return {
    attribute: data.action?.attribute ?? 'href',
    autoSave: data.action?.autoSave ?? true,
    breakpoint: data.breakpoint ?? false,
    continueOnError: data.action?.continueOnError ?? false,
    debugLog: false,
    description: data.description,
    extractMode: data.action?.extractMode ?? 'text',
    inputValue: data.action?.inputValue ?? '',
    inputVariable: data.action?.inputVariable ?? '',
    operation: data.action?.operation ?? data.action?.operator ?? 'trim',
    pattern: data.action?.pattern ?? '',
    checked: data.action?.checked ?? true,
    delimiter: data.action?.delimiter ?? ',',
    delayMs: data.action?.delayMs ?? (actionType === 'control.delay' ? 1000 : 0),
    distance: data.action?.distance ?? 800,
    left: data.action?.left ?? data.action?.leftVariable ?? '',
    right: data.action?.right ?? data.action?.rightVariable ?? '',
    method: data.action?.method ?? 'GET',
    message: data.action?.message ?? '',
    channel: data.action?.channel ?? '',
    defaultValue: data.action?.defaultValue ?? data.action?.value ?? '',
    logLevel: data.action?.logLevel ?? 'info',
    column: data.action?.column ?? '',
    content: data.action?.content ?? (actionType === 'excel.write' || actionType === 'excel.addrow' ? JSON.stringify(data.action?.rows ?? [], null, 0) : ''),
    code: data.action?.code ?? '',
    path: data.action?.path ?? data.action?.scriptPath ?? data.action?.filePath ?? data.action?.targetPath ?? getDefaultPath(actionType, data.kind, data.action?.code),
    preScreenshot: false,
    command: data.action?.command ?? '',
    errorVariable: data.action?.errorVariable ?? '',
    flowId: data.action?.flowId ?? '',
    fillMode: data.action?.fillMode === 'js' ? 'js' : data.action?.fillMode === 'type' ? 'type' : 'fill',
    retryCount: data.action?.retryCount ?? data.action?.maxIterations ?? 3,
    requestBody: data.action?.requestBody ?? '',
    responseVariable: data.action?.responseVariable ?? data.action?.outputVariable ?? data.action?.itemsVariable ?? '',
    firstValueVariable: data.action?.firstValueVariable ?? '',
    stderrVariable: data.action?.stderrVariable ?? 'script_stderr',
    itemVariable: data.action?.itemVariable ?? 'current_row',
    indexVariable: data.action?.indexVariable ?? (actionType === 'control.repeat_until' ? 'repeat_index' : 'loop_index'),
    maxIterations: data.action?.maxIterations ?? (actionType === 'control.repeat_until' ? 50 : 1000),
    selector: data.action?.selector ?? '#username',
    statusVariable: data.action?.statusVariable ?? data.action?.countVariable ?? '',
    targetPath: data.action?.targetPath ?? '',
    targetSelector: data.action?.targetSelector ?? '',
    tabIndex: data.action?.index ?? 0,
    targetUrl: data.action?.targetUrl ?? data.action?.url ?? '',
    timeoutSeconds: Math.max(1, Math.round((data.action?.timeoutMs ?? 30_000) / 1000)),
    title: data.title,
    variableName: data.action?.variableName ?? data.action?.inputVariable ?? data.action?.outputVariable ?? '',
    variableScope: data.action?.scope ?? '全局',
    humanTakeoverMessage: data.action?.humanTakeoverMessage ?? '',
    humanTakeoverResumeMode: data.action?.humanTakeoverResumeMode ?? 'next_node',
    fallbackSelectors: data.action?.fallbackSelectors ?? '',
    anchorText: data.action?.anchorText ?? '',
    outputSchema: data.action?.outputSchema ?? '',
    waitCondition: data.action?.waitCondition ?? 'visible'
  };
}

export function shouldUseOutputSchema(actionType: string | undefined): boolean {
  return (
    actionType === 'browser.extract' ||
    actionType === 'ui.extract' ||
    actionType === 'browser.clickLoadMore' ||
    actionType === 'browser.paginateNext'
  );
}

function shouldUseSelectorResilience(actionType: string | undefined): boolean {
  return (
    actionType === 'browser.click' ||
    actionType === 'browser.fill' ||
    actionType === 'browser.press' ||
    actionType === 'browser.wait' ||
    actionType === 'browser.waitFor' ||
    actionType === 'browser.extract' ||
    actionType === 'browser.check' ||
    actionType === 'browser.hover' ||
    actionType === 'browser.select' ||
    actionType === 'ui.click' ||
    actionType === 'ui.fill' ||
    actionType === 'ui.wait' ||
    actionType === 'ui.extract' ||
    actionType === 'ui.check' ||
    actionType === 'ui.select'
  );
}

function getDefaultPath(actionType: string, kind: RpaNodeData['kind'], existingCode?: string): string {
  if ((actionType.startsWith('script.') || kind === 'script') && existingCode) {
    return '';
  }
  if (actionType.startsWith('excel.') || kind === 'excel') {
    return 'data/orders.csv';
  }
  if (actionType.startsWith('file.') || kind === 'file') {
    return '${var.output_prefix}.txt';
  }
  if (actionType.startsWith('script.') || kind === 'script') {
    return actionType === 'script.javascript' ? 'scripts/transform.js' : 'scripts/data_clean.py';
  }
  return '';
}

/** 反向写回：每个字段只在其所属 action 类型下才落地，其余类型保留原值不变——顺序与 createNodeConfigDraft 的映射必须保持一致。 */
export function applyNodeConfigDraft(data: RpaNodeData, draft: RpaNodeConfigDraft): RpaNodeData {
  const actionType = data.action?.type;
  return {
    ...data,
    action: {
      ...data.action,
      autoSave: draft.autoSave,
      continueOnError: draft.continueOnError,
      attribute: shouldUseAttribute(actionType) ? (draft.attribute === '' ? undefined : draft.attribute) : data.action?.attribute,
      extractMode: draft.extractMode,
      inputValue: shouldUseInputValue(actionType) ? draft.inputValue : data.action?.inputValue,
      inputVariable: draft.inputVariable === '' ? undefined : draft.inputVariable,
      operation: data.action?.type?.startsWith('data.') && data.action.type !== 'data.math.compute' ? (draft.operation === '' ? undefined : draft.operation) : data.action?.operation,
      pattern: data.action?.type === 'data.regex.match' || data.action?.type === 'file.list' ? (draft.pattern === '' ? undefined : draft.pattern) : data.action?.pattern,
      checked: data.action?.type === 'ui.check' || data.action?.type === 'browser.check' ? draft.checked : data.action?.checked,
      delimiter: data.action?.type === 'data.string.transform' || data.action?.type === 'data.list.map' ? (draft.delimiter === '' ? undefined : draft.delimiter) : data.action?.delimiter,
      delayMs: data.action?.type === 'control.delay' || data.action?.type === 'control.retry' || data.action?.type === 'browser.clickLoadMore' || data.action?.type === 'browser.paginateNext' || data.action?.type === 'browser.dismiss' ? draft.delayMs : data.action?.delayMs,
      distance: data.action?.type === 'browser.scroll' ? draft.distance : data.action?.distance,
      left: data.action?.type === 'data.math.compute' ? (draft.left === '' ? undefined : draft.left) : data.action?.left,
      right: data.action?.type === 'data.math.compute' ? (draft.right === '' ? undefined : draft.right) : data.action?.right,
      operator: data.action?.type === 'data.math.compute' ? (draft.operation === '' ? undefined : draft.operation) : data.action?.operator,
      method: actionType === 'http.request' ? draft.method : data.action?.method,
      message: data.action?.type?.startsWith('variable.') || data.action?.type === 'script.websocket' ? (draft.message === '' ? undefined : draft.message) : data.action?.message,
      channel: data.action?.type === 'variable.notify' ? (draft.channel === '' ? undefined : draft.channel) : data.action?.channel,
      defaultValue: data.action?.type === 'variable.input' ? draft.defaultValue : data.action?.defaultValue,
      logLevel: data.action?.type === 'variable.log' ? draft.logLevel : data.action?.logLevel,
      scope: data.action?.type === 'variable.set' || data.action?.type === 'variable.input' ? draft.variableScope : data.action?.scope,
      variableName: data.action?.type?.startsWith('variable.') ? (draft.variableName === '' ? undefined : draft.variableName) : data.action?.variableName,
      value: data.action?.type === 'variable.set' ? draft.defaultValue : data.action?.value,
      column: actionType?.startsWith('excel.') === true ? (draft.column === '' ? undefined : draft.column) : data.action?.column,
      content: data.action?.type === 'variable.clipboard' ? (draft.content === '' ? undefined : draft.content) : draft.content === '' ? undefined : draft.content,
      requestBody: actionType === 'http.request' ? (draft.requestBody === '' ? undefined : draft.requestBody) : data.action?.requestBody,
      responseVariable: data.action?.type === 'control.foreach' ? data.action?.responseVariable : draft.responseVariable === '' ? undefined : draft.responseVariable,
      outputVariable: shouldMirrorOutputVariable(data.action?.type) ? (draft.responseVariable === '' ? undefined : draft.responseVariable) : data.action?.outputVariable,
      stderrVariable: data.action?.type?.startsWith('script.') ? (draft.stderrVariable === '' ? undefined : draft.stderrVariable) : data.action?.stderrVariable,
      itemsVariable: data.action?.type === 'control.foreach' ? (draft.responseVariable === '' ? undefined : draft.responseVariable) : data.action?.itemsVariable,
      itemVariable: data.action?.type === 'control.foreach' ? (draft.itemVariable === '' ? undefined : draft.itemVariable) : data.action?.itemVariable,
      indexVariable: data.action?.type === 'control.foreach' || data.action?.type === 'control.repeat_until' ? (draft.indexVariable === '' ? undefined : draft.indexVariable) : data.action?.indexVariable,
      maxIterations: data.action?.type === 'control.foreach' || data.action?.type === 'control.repeat_until' || data.action?.type === 'browser.clickLoadMore' || data.action?.type === 'browser.paginateNext' || data.action?.type === 'browser.dismiss' ? draft.maxIterations : data.action?.maxIterations,
      retryCount: data.action?.type === 'control.retry' ? draft.retryCount : data.action?.retryCount,
      errorVariable: data.action?.type === 'control.try' ? (draft.errorVariable === '' ? undefined : draft.errorVariable) : data.action?.errorVariable,
      flowId: data.action?.type === 'control.subprocess' ? (draft.flowId === '' ? undefined : draft.flowId) : data.action?.flowId,
      code: (data.action?.type === 'script.python' || data.action?.type === 'script.javascript') ? (draft.code === '' ? undefined : draft.code) : data.action?.code,
      command: data.action?.type === 'script.shell' ? (draft.command === '' ? undefined : draft.command) : data.action?.command,
      fillMode: data.action?.type === 'browser.fill' || data.action?.type === 'ui.fill' ? draft.fillMode === 'fill' ? undefined : draft.fillMode : data.action?.fillMode,
      selector: shouldUseSelector(actionType) ? draft.selector : data.action?.selector,
      targetSelector: data.action?.type === 'ui.drag' || data.action?.type === 'browser.drag' || data.action?.type === 'browser.clickLoadMore' || data.action?.type === 'browser.paginateNext' || data.action?.type === 'browser.dismiss' || data.action?.type === 'browser.ensureLogin' ? (draft.targetSelector === '' ? undefined : draft.targetSelector) : data.action?.targetSelector,
      index: data.action?.type === 'browser.tab.switch' || data.action?.type === 'excel.deleterow' ? draft.tabIndex : data.action?.index,
      path: shouldUsePath(actionType) ? (draft.path === '' ? undefined : draft.path) : data.action?.path,
      scriptPath: data.action?.type?.startsWith('script.') ? (draft.path === '' ? undefined : draft.path) : data.action?.scriptPath,
      targetPath: data.action?.type === 'file.copy' || data.action?.type === 'file.move' || data.action?.type === 'file.compress' || data.action?.type === 'file.rename' ? (draft.targetPath === '' ? undefined : draft.targetPath) : data.action?.targetPath,
      countVariable: shouldUseCountVariable(actionType) ? (draft.statusVariable === '' ? undefined : draft.statusVariable) : data.action?.countVariable,
      rows: data.action?.type === 'excel.write' || data.action?.type === 'excel.addrow' ? parseRows(draft.content) : data.action?.rows,
      targetUrl: shouldUseTargetUrl(actionType) ? (draft.targetUrl === '' ? undefined : draft.targetUrl) : data.action?.targetUrl,
      timeoutMs: draft.timeoutSeconds * 1000,
      type: data.action?.type ?? `${data.kind}.custom`,
      url: data.action?.type === 'http.request' || data.action?.type === 'script.websocket' ? (draft.targetUrl === '' ? undefined : draft.targetUrl) : data.action?.url,
      statusVariable: draft.statusVariable === '' ? undefined : draft.statusVariable,
      humanTakeoverMessage: data.action?.type === 'control.human_takeover' ? (draft.humanTakeoverMessage === '' ? undefined : draft.humanTakeoverMessage) : data.action?.humanTakeoverMessage,
      humanTakeoverResumeMode: data.action?.type === 'control.human_takeover' ? draft.humanTakeoverResumeMode : data.action?.humanTakeoverResumeMode,
      firstValueVariable: actionType === 'browser.ensureLogin' ? (draft.statusVariable === '' ? undefined : draft.statusVariable) : shouldUseFirstValueVariable(actionType) ? (draft.firstValueVariable === '' ? undefined : draft.firstValueVariable) : data.action?.firstValueVariable,
      fallbackSelectors: shouldUseSelectorResilience(actionType) ? (draft.fallbackSelectors === '' ? undefined : draft.fallbackSelectors) : data.action?.fallbackSelectors,
      anchorText: shouldUseSelectorResilience(actionType) ? (draft.anchorText === '' ? undefined : draft.anchorText) : data.action?.anchorText,
      outputSchema: shouldUseOutputSchema(actionType) ? (draft.outputSchema.trim() === '' ? undefined : draft.outputSchema) : data.action?.outputSchema,
      waitCondition: actionType === 'browser.waitFor' ? draft.waitCondition : data.action?.waitCondition
    },
    breakpoint: draft.breakpoint,
    description: draft.description,
    title: draft.title
  };
}

function parseRows(value: string): unknown[] | undefined {
  if (value.trim() === '') {
    return undefined;
  }
  try {
    const decoded = JSON.parse(value) as unknown;
    return Array.isArray(decoded) ? decoded : [value];
  } catch {
    return [value];
  }
}

function shouldMirrorOutputVariable(actionType: string | undefined): boolean {
  if (actionType === undefined || actionType === 'control.foreach') {
    return false;
  }
  return (
    actionType.startsWith('browser.') ||
    actionType.startsWith('ui.') ||
    actionType.startsWith('data.') ||
    actionType.startsWith('file.') ||
    actionType.startsWith('excel.') ||
    actionType.startsWith('script.') ||
    actionType.startsWith('variable.') ||
    actionType === 'control.delay' ||
    actionType === 'control.retry' ||
    actionType === 'control.try' ||
    actionType === 'control.subprocess'
  );
}

function shouldUseFirstValueVariable(actionType: string | undefined): boolean {
  return (
    actionType === 'browser.extract' ||
    actionType === 'ui.extract' ||
    actionType === 'browser.fetch' ||
    actionType === 'browser.clickLoadMore' ||
    actionType === 'browser.paginateNext'
  );
}

function shouldUseCountVariable(actionType: string | undefined): boolean {
  return (
    shouldUseFirstValueVariable(actionType) ||
    actionType?.startsWith('excel.') === true ||
    actionType?.startsWith('file.') === true ||
    actionType?.startsWith('data.') === true
  );
}

function shouldUseInputValue(actionType: string | undefined): boolean {
  return (
    actionType === 'browser.fill' ||
    actionType === 'browser.press' ||
    actionType === 'browser.waitFor' ||
    actionType === 'ui.fill' ||
    actionType === 'browser.select' ||
    actionType === 'ui.select' ||
    actionType === 'control.condition' ||
    actionType === 'control.repeat_until' ||
    actionType === 'data.string.transform' ||
    actionType === 'data.regex.match'
  );
}

function shouldUseSelector(actionType: string | undefined): boolean {
  return (
    actionType === 'browser.fetch' ||
    actionType === 'browser.ensureLogin' ||
    actionType === 'browser.click' ||
    actionType === 'browser.fill' ||
    actionType === 'browser.press' ||
    actionType === 'browser.wait' ||
    actionType === 'browser.waitFor' ||
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

function shouldUsePath(actionType: string | undefined): boolean {
  return (
    actionType?.startsWith('file.') === true ||
    actionType?.startsWith('excel.') === true ||
    actionType?.startsWith('script.') === true
  );
}

function shouldUseTargetUrl(actionType: string | undefined): boolean {
  return actionType === 'browser.fetch' || actionType === 'browser.open' || actionType === 'browser.tab.open' || actionType === 'browser.ensureLogin';
}

function shouldUseAttribute(actionType: string | undefined): boolean {
  return actionType === 'browser.extract' || actionType === 'ui.extract' || actionType === 'browser.fetch' || actionType === 'browser.clickLoadMore' || actionType === 'browser.paginateNext';
}
