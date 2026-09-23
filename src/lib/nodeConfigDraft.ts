import { DEFAULT_ACTION_TYPE_BY_KIND, type RpaNodeAction, type RpaNodeConfigDraft, type RpaNodeData } from '../types/rpa';

/** 把节点的 action（按类型才有的字段）展平成配置面板统一的草稿表单；新增 action 字段需同时更新 applyNodeConfigDraft 做反向写回。 */
export function createNodeConfigDraft(data: RpaNodeData): RpaNodeConfigDraft {
  const action: Partial<RpaNodeAction> = data.action ?? {};
  const actionType = action.type ?? DEFAULT_ACTION_TYPE_BY_KIND[data.kind];
  return {
    attribute: action.attribute ?? 'href',
    autoSave: action.autoSave ?? true,
    breakpoint: data.breakpoint ?? false,
    continueOnError: action.continueOnError ?? false,
    requireConfirmation: action.requireConfirmation === true,
    debugLog: false,
    description: data.description,
    extractMode: action.extractMode ?? 'text',
    inputValue: action.inputValue ?? '',
    inputVariable: action.inputVariable ?? '',
    operation: action.operation ?? action.operator ?? 'trim',
    pattern: action.pattern ?? '',
    checked: action.checked ?? true,
    delimiter: action.delimiter ?? ',',
    delayMs: action.delayMs ?? (actionType === 'control.delay' ? 1000 : 0),
    distance: action.distance ?? 800,
    left: action.left ?? action.leftVariable ?? '',
    right: action.right ?? action.rightVariable ?? '',
    method: action.method ?? 'GET',
    message: action.message ?? '',
    channel: action.channel ?? '',
    defaultValue: action.defaultValue ?? action.value ?? '',
    logLevel: action.logLevel ?? 'info',
    column: action.column ?? '',
    content: action.content ?? (actionType === 'excel.write' || actionType === 'excel.addrow' ? JSON.stringify(action.rows ?? [], null, 0) : ''),
    code: action.code ?? '',
    path: action.path ?? action.scriptPath ?? action.filePath ?? action.targetPath ?? getDefaultPath(actionType, data.kind, action.code),
    preScreenshot: false,
    command: action.command ?? '',
    errorVariable: action.errorVariable ?? '',
    flowId: action.flowId ?? '',
    // fillMode 白名单不能省成 ?? 'fill'：草稿里出现界面没有的模式，写回时会当成用户选过的值落进定义
    fillMode: action.fillMode === 'js' || action.fillMode === 'type' ? action.fillMode : 'fill',
    retryCount: action.retryCount ?? action.maxIterations ?? 3,
    requestBody: action.requestBody ?? '',
    responseVariable: action.responseVariable ?? action.outputVariable ?? action.itemsVariable ?? '',
    firstValueVariable: action.firstValueVariable ?? '',
    stderrVariable: action.stderrVariable ?? 'script_stderr',
    itemVariable: action.itemVariable ?? 'current_row',
    indexVariable: action.indexVariable ?? (actionType === 'control.repeat_until' ? 'repeat_index' : 'loop_index'),
    maxIterations: action.maxIterations ?? (actionType === 'control.repeat_until' ? 50 : 1000),
    selector: action.selector ?? '#username',
    statusVariable: action.statusVariable ?? action.countVariable ?? '',
    targetPath: action.targetPath ?? '',
    targetSelector: action.targetSelector ?? '',
    tabIndex: action.index ?? action.rowIndex ?? 0,
    targetUrl: action.targetUrl ?? action.url ?? '',
    timeoutSeconds: Math.max(1, Math.round((action.timeoutMs ?? 30_000) / 1000)),
    title: data.title,
    variableName: action.variableName ?? action.inputVariable ?? action.outputVariable ?? '',
    variableScope: action.scope ?? '全局',
    fallbackSelectors: action.fallbackSelectors ?? '',
    anchorText: action.anchorText ?? '',
    outputSchema: action.outputSchema ?? '',
    urlTemplate: action.urlTemplate ?? '',
    startPage: action.startPage ?? 1,
    pageStep: action.pageStep ?? 1,
    waitCondition: action.waitCondition ?? 'visible'
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
  const previous = data.action;
  const is = (...types: string[]): boolean => actionType !== undefined && types.includes(actionType);
  const isKind = (prefix: string): boolean => actionType?.startsWith(prefix) === true;
  const urlPaging = draft.urlTemplate.trim();
  return {
    ...data,
    action: {
      ...previous,
      autoSave: draft.autoSave,
      continueOnError: draft.continueOnError,
      // 关闭时写 undefined 不留 false：这个标记会跟着流程定义进到 AI 看到的节点上，一个后端永远不读的 false 只是噪声。
      requireConfirmation: shouldUseRequireConfirmation(actionType) ? (draft.requireConfirmation ? true : undefined) : previous?.requireConfirmation,
      attribute: shouldUseAttribute(actionType) ? text(draft.attribute) : previous?.attribute,
      extractMode: draft.extractMode,
      inputValue: shouldUseInputValue(actionType) ? draft.inputValue : previous?.inputValue,
      inputVariable: text(draft.inputVariable),
      operation: isKind('data.') && !is('data.math.compute') ? text(draft.operation) : previous?.operation,
      pattern: is('data.regex.match', 'file.list') ? text(draft.pattern) : previous?.pattern,
      checked: is('ui.check', 'browser.check') ? draft.checked : previous?.checked,
      delimiter: is('data.string.transform', 'data.list.map') ? text(draft.delimiter) : previous?.delimiter,
      delayMs: is('control.delay', 'control.retry', 'browser.clickLoadMore', 'browser.paginateNext', 'browser.dismiss') ? draft.delayMs : previous?.delayMs,
      distance: is('browser.scroll') ? draft.distance : previous?.distance,
      left: is('data.math.compute') ? text(draft.left) : previous?.left,
      right: is('data.math.compute') ? text(draft.right) : previous?.right,
      operator: is('data.math.compute') ? text(draft.operation) : previous?.operator,
      method: is('http.request') ? draft.method : previous?.method,
      message: isKind('variable.') || is('script.websocket') ? text(draft.message) : previous?.message,
      channel: is('variable.notify') ? text(draft.channel) : previous?.channel,
      defaultValue: previous?.defaultValue,
      logLevel: is('variable.log') ? draft.logLevel : previous?.logLevel,
      scope: is('variable.set') ? draft.variableScope : previous?.scope,
      variableName: isKind('variable.') ? text(draft.variableName) : previous?.variableName,
      value: is('variable.set') ? draft.defaultValue : previous?.value,
      column: isKind('excel.') ? text(draft.column) : previous?.column,
      content: text(draft.content),
      requestBody: is('http.request') ? text(draft.requestBody) : previous?.requestBody,
      responseVariable: is('control.foreach') ? previous?.responseVariable : text(draft.responseVariable),
      outputVariable: shouldMirrorOutputVariable(actionType) ? text(draft.responseVariable) : previous?.outputVariable,
      stderrVariable: isKind('script.') ? text(draft.stderrVariable) : previous?.stderrVariable,
      itemsVariable: is('control.foreach') ? text(draft.responseVariable) : previous?.itemsVariable,
      itemVariable: is('control.foreach') ? text(draft.itemVariable) : previous?.itemVariable,
      indexVariable: is('control.foreach', 'control.repeat_until') ? text(draft.indexVariable) : previous?.indexVariable,
      maxIterations: is('control.foreach', 'control.repeat_until', 'browser.clickLoadMore', 'browser.paginateNext', 'browser.dismiss') ? draft.maxIterations : previous?.maxIterations,
      // startPage/pageStep 只在 URL 式翻页下有意义：留着它们会让点击式节点带上永远不生效的字段，
      // 用户和 AI 都会以为改了页号却毫无效果
      urlTemplate: is('browser.paginateNext') ? text(urlPaging) : previous?.urlTemplate,
      startPage: is('browser.paginateNext') ? (urlPaging === '' ? undefined : draft.startPage) : previous?.startPage,
      pageStep: is('browser.paginateNext') ? (urlPaging === '' ? undefined : draft.pageStep) : previous?.pageStep,
      retryCount: is('control.retry') ? draft.retryCount : previous?.retryCount,
      errorVariable: is('control.try') ? text(draft.errorVariable) : previous?.errorVariable,
      flowId: is('control.subprocess') ? text(draft.flowId) : previous?.flowId,
      code: is('script.python', 'script.javascript') ? text(draft.code) : previous?.code,
      command: is('script.shell') ? text(draft.command) : previous?.command,
      fillMode: is('browser.fill', 'ui.fill') ? (draft.fillMode === 'fill' ? undefined : draft.fillMode) : previous?.fillMode,
      selector: shouldUseSelector(actionType) ? draft.selector : previous?.selector,
      targetSelector: is('ui.drag', 'browser.drag', 'browser.clickLoadMore', 'browser.paginateNext', 'browser.dismiss', 'browser.ensureLogin') ? text(draft.targetSelector) : previous?.targetSelector,
      index: is('browser.tab.switch') ? draft.tabIndex : previous?.index,
      rowIndex: is('excel.deleterow') ? draft.tabIndex : previous?.rowIndex,
      path: shouldUsePath(actionType) ? text(draft.path) : previous?.path,
      scriptPath: isKind('script.') ? text(draft.path) : previous?.scriptPath,
      targetPath: is('file.copy', 'file.move', 'file.compress', 'file.rename') ? text(draft.targetPath) : previous?.targetPath,
      countVariable: shouldUseCountVariable(actionType) ? text(draft.statusVariable) : previous?.countVariable,
      rows: is('excel.write', 'excel.addrow') ? parseRows(draft.content) : previous?.rows,
      targetUrl: shouldUseTargetUrl(actionType) ? text(draft.targetUrl) : previous?.targetUrl,
      timeoutMs: draft.timeoutSeconds * 1000,
      type: actionType ?? `${data.kind}.custom`,
      url: is('http.request', 'script.websocket') ? text(draft.targetUrl) : previous?.url,
      statusVariable: text(draft.statusVariable),
      // ensureLogin 把登录状态写进 firstValueVariable，用的是同一个「状态变量」输入框
      firstValueVariable: is('browser.ensureLogin')
        ? text(draft.statusVariable)
        : shouldUseFirstValueVariable(actionType) ? text(draft.firstValueVariable) : previous?.firstValueVariable,
      fallbackSelectors: shouldUseSelectorResilience(actionType) ? text(draft.fallbackSelectors) : previous?.fallbackSelectors,
      anchorText: shouldUseSelectorResilience(actionType) ? text(draft.anchorText) : previous?.anchorText,
      outputSchema: shouldUseOutputSchema(actionType) ? (draft.outputSchema.trim() === '' ? undefined : draft.outputSchema) : previous?.outputSchema,
      waitCondition: is('browser.waitFor') ? draft.waitCondition : previous?.waitCondition
    },
    breakpoint: draft.breakpoint,
    description: draft.description,
    title: draft.title
  };
}

/** 空串写回 undefined 而不是 ''：字段会跟着流程定义走到后端和 AI 面前，空串会被读成「配了一个空值」。 */
function text(value: string): string | undefined {
  return value === '' ? undefined : value;
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

/** 人工确认闸门只由插件执行器实现（task_manager._maybe_confirm_sensitive_action），而它只接浏览器
 *  动作：非浏览器节点打上这个标记不会有任何人被问到。 */
export function shouldUseRequireConfirmation(actionType: string | undefined): boolean {
  return actionType !== undefined && (actionType.startsWith('browser.') || actionType.startsWith('ui.'));
}

function shouldUseAttribute(actionType: string | undefined): boolean {
  return actionType === 'browser.extract' || actionType === 'ui.extract' || actionType === 'browser.fetch' || actionType === 'browser.clickLoadMore' || actionType === 'browser.paginateNext';
}
