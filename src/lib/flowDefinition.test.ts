import { describe, expect, it } from 'vitest';

import { createFlowNode } from './flowOperations';
import { buildFlowDefinition, readFlowInputVariables, restoreFlowCanvas } from './flowDefinition';

describe('flowDefinition', () => {
  it('序列化并恢复 HTTP 请求节点动作', () => {
    const node = createFlowNode({ label: 'HTTP 请求', nodeType: 'script' }, { x: 120, y: 180 }, 1);
    const definition = buildFlowDefinition([node], []);
    const restored = restoreFlowCanvas(definition);

    expect(definition.nodes).toEqual([
      expect.objectContaining({
        type: 'http.request',
        method: 'GET',
        url: 'https://api.example.com/data',
        responseVariable: 'http_response',
        statusVariable: 'http_status',
        jsonVariable: 'http_json'
      })
    ]);
    expect(restored?.nodes[0]?.data.action).toEqual(
      expect.objectContaining({
        type: 'http.request',
        method: 'GET',
        url: 'https://api.example.com/data',
        responseVariable: 'http_response',
        statusVariable: 'http_status',
        jsonVariable: 'http_json'
      })
    );
  });

  it('序列化并恢复浏览器动作节点', () => {
    const fillNode = createFlowNode({ label: '输入文本', nodeType: 'browser' }, { x: 120, y: 180 }, 1);
    const clickNode = createFlowNode({ label: '点击元素', nodeType: 'browser' }, { x: 120, y: 280 }, 2);
    const extractNode = createFlowNode({ label: '获取文本', nodeType: 'browser' }, { x: 120, y: 380 }, 3);
    const definition = buildFlowDefinition([fillNode, clickNode, extractNode], []);
    const restored = restoreFlowCanvas(definition);

    expect(definition.nodes).toEqual([
      expect.objectContaining({ type: 'browser.fill', selector: '#username', inputValue: '${var.username}' }),
      expect.objectContaining({ type: 'browser.click', selector: '#submit' }),
      expect.objectContaining({ type: 'browser.extract', selector: '.result', outputVariable: 'browser_texts', firstValueVariable: 'browser_text' })
    ]);
    expect(restored?.nodes.map((node) => node.data.action?.type)).toEqual(['browser.fill', 'browser.click', 'browser.extract']);
    expect(restored?.nodes[2]?.data.action).toEqual(expect.objectContaining({ outputVariable: 'browser_texts', firstValueVariable: 'browser_text' }));
  });

  it('序列化并恢复累计输出配置', () => {
    const extractNode = createFlowNode({ label: '获取文本', nodeType: 'browser' }, { x: 120, y: 180 }, 1);
    expect(extractNode.data.action).toBeDefined();
    extractNode.data.action = {
      ...extractNode.data.action!,
      appendMode: 'record',
      appendVariable: 'all_detail_records',
      outputVariable: 'detail_texts'
    };

    const definition = buildFlowDefinition([extractNode], []);
    const restored = restoreFlowCanvas(definition);
    const nodes = definition.nodes as Array<Record<string, unknown>>;

    expect(nodes[0]).toEqual(
      expect.objectContaining({
        appendMode: 'record',
        appendVariable: 'all_detail_records',
        outputVariable: 'detail_texts'
      })
    );
    expect(restored?.nodes[0]?.data.action).toEqual(
      expect.objectContaining({
        appendMode: 'record',
        appendVariable: 'all_detail_records',
        outputVariable: 'detail_texts'
      })
    );
  });

  it('序列化并恢复 Excel 与文件动作节点', () => {
    const excelNode = createFlowNode({ label: '读取单元格', nodeType: 'excel' }, { x: 120, y: 180 }, 1);
    const fileNode = createFlowNode({ label: '写入文件', nodeType: 'file' }, { x: 120, y: 280 }, 2);
    const listNode = createFlowNode({ label: '遍历文件夹', nodeType: 'file' }, { x: 120, y: 380 }, 3);
    const copyNode = createFlowNode({ label: '复制/移动', nodeType: 'file' }, { x: 120, y: 480 }, 4);
    const definition = buildFlowDefinition([excelNode, fileNode, listNode, copyNode], []);
    const restored = restoreFlowCanvas(definition);

    expect(definition.nodes).toEqual([
      expect.objectContaining({
        type: 'excel.read',
        path: 'data/orders.csv',
        column: 'order_id',
        countVariable: 'row_count'
      }),
      expect.objectContaining({
        type: 'file.write',
        path: '${var.output_prefix}.txt',
        content: '${var.first_order_id}'
      }),
      expect.objectContaining({
        type: 'file.list',
        path: 'data',
        pattern: '*.txt',
        outputVariable: 'file_paths',
        countVariable: 'file_count'
      }),
      expect.objectContaining({
        type: 'file.copy',
        path: 'data/input.txt',
        targetPath: 'archive/input.txt',
        outputVariable: 'copied_path'
      })
    ]);
    expect(restored?.nodes.map((node) => node.data.action?.type)).toEqual(['excel.read', 'file.write', 'file.list', 'file.copy']);
    expect(restored?.nodes[0]?.data.action).toEqual(expect.objectContaining({ path: 'data/orders.csv', column: 'order_id' }));
    expect(restored?.nodes[1]?.data.action).toEqual(expect.objectContaining({ path: '${var.output_prefix}.txt', content: '${var.first_order_id}' }));
    expect(restored?.nodes[2]?.data.action).toEqual(expect.objectContaining({ path: 'data', pattern: '*.txt' }));
    expect(restored?.nodes[3]?.data.action).toEqual(expect.objectContaining({ targetPath: 'archive/input.txt' }));
  });

  it('序列化并恢复循环控制节点', () => {
    const loopNode = createFlowNode({ label: '遍历列表', nodeType: 'control' }, { x: 120, y: 180 }, 1);
    const definition = buildFlowDefinition([loopNode], []);
    const restored = restoreFlowCanvas(definition);

    expect(definition.nodes).toEqual([
      expect.objectContaining({
        type: 'control.foreach',
        itemsVariable: 'excel_rows',
        itemVariable: 'current_row',
        indexVariable: 'loop_index',
        maxIterations: 1000
      })
    ]);
    expect(restored?.nodes[0]?.data.action).toEqual(
      expect.objectContaining({
        type: 'control.foreach',
        itemsVariable: 'excel_rows',
        itemVariable: 'current_row',
        indexVariable: 'loop_index',
        maxIterations: 1000
      })
    );
  });

  it('序列化并恢复 UI 自动化和控制动作节点', () => {
    const uiFillNode = createFlowNode({ label: '输入文字', nodeType: 'ui' }, { x: 120, y: 180 }, 1);
    const uiDragNode = createFlowNode({ label: '拖拽操作', nodeType: 'ui' }, { x: 120, y: 280 }, 2);
    const delayNode = createFlowNode({ label: '等待延时', nodeType: 'control' }, { x: 120, y: 380 }, 3);
    const breakNode = createFlowNode({ label: '中断循环', nodeType: 'control' }, { x: 120, y: 480 }, 4);
    const definition = buildFlowDefinition([uiFillNode, uiDragNode, delayNode, breakNode], []);
    const restored = restoreFlowCanvas(definition);

    expect(definition.nodes).toEqual([
      expect.objectContaining({ type: 'ui.fill', selector: '#username', inputValue: '${var.username}' }),
      expect.objectContaining({ type: 'ui.drag', selector: '#source', targetSelector: '#target', outputVariable: 'drop_target' }),
      expect.objectContaining({ type: 'control.delay', delayMs: 1000, outputVariable: 'delay_ms' }),
      expect.objectContaining({ type: 'control.break' })
    ]);
    expect(restored?.nodes.map((node) => node.data.action?.type)).toEqual(['ui.fill', 'ui.drag', 'control.delay', 'control.break']);
    expect(restored?.nodes[1]?.data.action).toEqual(expect.objectContaining({ targetSelector: '#target' }));
    expect(restored?.nodes[2]?.data.action).toEqual(expect.objectContaining({ delayMs: 1000 }));
  });

  it('序列化并恢复浏览器扩展动作节点', () => {
    const scrollNode = createFlowNode({ label: '滚动页面', nodeType: 'browser' }, { x: 120, y: 180 }, 1);
    const tabNode = createFlowNode({ label: '切换标签页', nodeType: 'browser' }, { x: 120, y: 280 }, 2);
    const definition = buildFlowDefinition([scrollNode, tabNode], []);
    const restored = restoreFlowCanvas(definition);

    expect(definition.nodes).toEqual([
      expect.objectContaining({ type: 'browser.scroll', distance: 800 }),
      expect.objectContaining({ type: 'browser.tab.switch', index: 0 })
    ]);
    expect(restored?.nodes.map((node) => node.data.action?.type)).toEqual(['browser.scroll', 'browser.tab.switch']);
    expect(restored?.nodes[0]?.data.action).toEqual(expect.objectContaining({ distance: 800 }));
    expect(restored?.nodes[1]?.data.action).toEqual(expect.objectContaining({ index: 0 }));
  });

  it('序列化并恢复变量与消息动作节点', () => {
    const setNode = createFlowNode({ label: '赋值变量', nodeType: 'variable' }, { x: 120, y: 180 }, 1);
    const getNode = createFlowNode({ label: '获取变量', nodeType: 'variable' }, { x: 120, y: 280 }, 2);
    const logNode = createFlowNode({ label: '输出日志', nodeType: 'variable' }, { x: 120, y: 380 }, 3);
    const notifyNode = createFlowNode({ label: '消息通知', nodeType: 'variable' }, { x: 120, y: 480 }, 4);
    const clipboardNode = createFlowNode({ label: '剪贴板', nodeType: 'variable' }, { x: 120, y: 580 }, 5);
    const inputNode = createFlowNode({ label: '输入弹窗', nodeType: 'variable' }, { x: 120, y: 680 }, 6);
    const definition = buildFlowDefinition([setNode, getNode, logNode, notifyNode, clipboardNode, inputNode], []);
    const restored = restoreFlowCanvas(definition);

    expect(definition.nodes).toEqual([
      expect.objectContaining({ type: 'variable.set', variableName: 'result_status', value: 'done', scope: '全局', outputVariable: 'result_status' }),
      expect.objectContaining({ type: 'variable.get', variableName: 'result_status', outputVariable: 'status_value' }),
      expect.objectContaining({ type: 'variable.log', message: '处理结果: ${var.result_status}', logLevel: 'info' }),
      expect.objectContaining({ type: 'variable.notify', channel: '企业微信', message: '流程执行完成: ${var.result_status}', outputVariable: 'notification_message' }),
      expect.objectContaining({ type: 'variable.clipboard', content: '${var.result_status}', outputVariable: 'clipboard_text' }),
      expect.objectContaining({ type: 'variable.input', variableName: 'user_input', message: '请输入运行参数', defaultValue: '', scope: '全局' })
    ]);
    expect(restored?.nodes.map((node) => node.data.action?.type)).toEqual(['variable.set', 'variable.get', 'variable.log', 'variable.notify', 'variable.clipboard', 'variable.input']);
    expect(restored?.nodes[2]?.data.action).toEqual(expect.objectContaining({ message: '处理结果: ${var.result_status}', logLevel: 'info' }));
    expect(restored?.nodes[3]?.data.action).toEqual(expect.objectContaining({ channel: '企业微信', outputVariable: 'notification_message' }));
  });

  it('序列化并恢复脚本动作节点', () => {
    const pythonNode = createFlowNode({ label: '执行 Python', nodeType: 'script' }, { x: 120, y: 180 }, 1);
    const jsNode = createFlowNode({ label: '执行 JavaScript', nodeType: 'script' }, { x: 120, y: 280 }, 2);
    const definition = buildFlowDefinition([pythonNode, jsNode], []);
    const restored = restoreFlowCanvas(definition);

    expect(definition.nodes).toEqual([
      expect.objectContaining({
        type: 'script.python',
        path: 'scripts/data_clean.py',
        outputVariable: 'script_stdout',
        statusVariable: 'script_exit_code',
        stderrVariable: 'script_stderr'
      }),
      expect.objectContaining({
        type: 'script.javascript',
        path: 'scripts/transform.js',
        outputVariable: 'script_stdout',
        statusVariable: 'script_exit_code',
        stderrVariable: 'script_stderr'
      })
    ]);
    expect(restored?.nodes.map((node) => node.data.action?.type)).toEqual(['script.python', 'script.javascript']);
    expect(restored?.nodes[0]?.data.action).toEqual(expect.objectContaining({ path: 'scripts/data_clean.py', stderrVariable: 'script_stderr' }));
    expect(restored?.nodes[1]?.data.action).toEqual(expect.objectContaining({ path: 'scripts/transform.js' }));
  });

  it('序列化并恢复数据处理节点', () => {
    const jsonNode = createFlowNode({ label: 'JSON 解析', nodeType: 'data' }, { x: 120, y: 180 }, 1);
    const regexNode = createFlowNode({ label: '正则匹配', nodeType: 'data' }, { x: 120, y: 280 }, 2);
    const mathNode = createFlowNode({ label: '数字运算', nodeType: 'data' }, { x: 120, y: 380 }, 3);
    const definition = buildFlowDefinition([jsonNode, regexNode, mathNode], []);
    const restored = restoreFlowCanvas(definition);

    expect(definition.nodes).toEqual([
      expect.objectContaining({ type: 'data.json.parse', inputVariable: 'http_response', outputVariable: 'parsed_json' }),
      expect.objectContaining({ type: 'data.regex.match', inputValue: '${var.input_text}', pattern: '(\\d+)', outputVariable: 'regex_matches' }),
      expect.objectContaining({ type: 'data.math.compute', left: '${var.left}', right: '${var.right}', operator: 'add', outputVariable: 'math_result' })
    ]);
    expect(restored?.nodes.map((node) => node.data.action?.type)).toEqual(['data.json.parse', 'data.regex.match', 'data.math.compute']);
    expect(restored?.nodes[1]?.data.action).toEqual(expect.objectContaining({ pattern: '(\\d+)', firstValueVariable: 'first_match' }));
    expect(restored?.nodes[2]?.data.action).toEqual(expect.objectContaining({ left: '${var.left}', right: '${var.right}', operator: 'add' }));
  });

  it('应保留流程变量分类与敏感标记并可恢复', () => {
    const definition = buildFlowDefinition(
      [],
      [],
      [
        { category: 'flow', name: 'order_id', sensitive: false, scope: '全局', type: 'String', value: 'A-1001' },
        { category: 'environment', name: 'run_scope', sensitive: false, scope: '全局', type: 'String', value: 'staging' },
        { category: 'credential', name: 'erp_password', sensitive: true, scope: '全局', type: 'String', value: 'secret-value' }
      ]
    );

    expect(definition.inputVariables).toEqual([
      expect.objectContaining({
        category: 'flow',
        name: 'order_id',
        scope: '全局',
        type: 'String',
        value: 'A-1001'
      }),
      expect.objectContaining({
        category: 'environment',
        name: 'run_scope',
        scope: '全局',
        type: 'String',
        value: 'staging'
      }),
      expect.objectContaining({
        category: 'credential',
        name: 'erp_password',
        sensitive: true,
        scope: '全局',
        type: 'String',
        value: 'secret-value'
      })
    ]);

    expect(readFlowInputVariables(definition)).toEqual([
      { category: 'flow', name: 'order_id', sensitive: false, scope: '全局', type: 'String', value: 'A-1001' },
      { category: 'environment', name: 'run_scope', sensitive: false, scope: '全局', type: 'String', value: 'staging' },
      { category: 'credential', name: 'erp_password', sensitive: true, scope: '全局', type: 'String', value: 'secret-value' }
    ]);
  });
});
