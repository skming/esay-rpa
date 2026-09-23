"""平台真正会读的节点字段名单，用于判定「写了没人读的键」。

名单是手工维护的常量，但不允许手工推导：tests/test_node_fields.py 会重新扫描执行层
与前端 flowDefinition.ts，对不上就红。加节点字段时先让那条测试红，再把新字段补进来，
这样名单不会随执行层演进悄悄落后——落后的代价是对合法流程报误判。

只收「平台任一层会读」的键：后端执行层、normalize/lint 等编排层、前端画布。三处都没有
才算无人读取。少收一个字段就会把合法流程判成错，所以宁可多收。
"""
from __future__ import annotations

KNOWN_NODE_FIELDS = frozenset({
    "action", "actionType", "adaptive", "anchorText", "appendMode", "appendOutputVariable",
    "appendVariable", "attribute", "autoSave", "body", "breakpoint", "channel", "checked",
    "clearCookies", "clearStorage", "code", "column", "command", "condition", "config",
    "content", "continueOnError", "continueOnMaxIterations", "countVariable", "data",
    "defaultValue", "delayMs", "delimiter", "description", "destinationPath", "disabled",
    "dismissedCountVariable", "distance", "durationMs", "endpoint", "errorVariable",
    "exitCodeVariable", "expression", "extractMode", "extractSelector", "fallbackSelectors",
    "fetcher", "filePath", "fillMode", "firstValueVariable", "flowId", "force", "headers",
    "id", "includeInResult", "index",
    "indexVariable", "inputValue", "inputVariable", "inputVariables", "itemSelector",
    "itemVariable", "items", "itemsVariable", "jsonVariable", "key", "kind", "label", "left",
    "leftVariable", "level", "limit", "listVariable", "loadedCountVariable", "logLevel",
    "maxIterations", "message", "method", "name", "operation", "operator", "outputSchema",
    "outputVariable", "outputVariables", "pageCountVariable", "pageStep", "path", "pattern",
    "position", "replacement", "requestBody", "requireConfirmation", "responseVariable",
    "resultVariable", "retryCount", "right", "rightVariable", "row", "rowData", "rowIndex",
    "rows", "saveAs", "scope", "scriptPath", "search", "selector", "sheetName", "sourceHandle",
    "startPage", "status", "statusCodeVariable", "statusVariable", "stderrVariable", "tabIndex",
    "target", "targetHandle", "targetPath", "targetSelector", "targetUrl", "timeoutMs", "title",
    "toPath", "trustedInput", "type", "url", "urlTemplate", "value", "variableName",
    "waitCondition",
})

# 同一件事的几种写法，模型猜错键名时基本落在这里；报错要能直接给出该改成什么。
FIELD_ALIAS_HINTS = {
    "maxPages": "maxIterations",
    "pageLimit": "maxIterations",
    "maxPage": "maxIterations",
    "limitPages": "maxIterations",
    "maxCount": "maxIterations",
    "waitMs": "delayMs",
    "intervalMs": "delayMs",
    "timeout": "timeoutMs",
    "text": "inputValue",
    "keyword": "inputValue",
    "variable": "outputVariable",
    "saveTo": "outputVariable",
    "resultVar": "outputVariable",
    "css": "selector",
    "xpath": "selector",
    "itemsSelector": "targetSelector",
    "script": "code",
    "sql": "code",
}
