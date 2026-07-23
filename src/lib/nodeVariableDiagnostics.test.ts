import { describe, expect, it } from 'vitest';
import type { Node } from '@xyflow/react';

import { buildNodeVariableDiagnostics, getIoFieldStatus } from './nodeVariableDiagnostics';
import type { RpaNodeData } from '../types/rpa';

function createNode(action: RpaNodeData['action']): Node<RpaNodeData> {
  return {
    id: 'n1',
    position: { x: 0, y: 0 },
    type: 'rpaStep',
    data: {
      title: '节点',
      description: '',
      kind: 'data',
      status: 'pending',
      action
    }
  };
}

describe('nodeVariableDiagnostics', () => {
  it('识别缺失引用变量', () => {
    const diagnostics = buildNodeVariableDiagnostics(
      createNode({ type: 'data.json.parse', inputVariable: 'missing_input', outputVariable: 'parsed_json' }),
      ['known_value']
    );

    expect(diagnostics.inputIssues[0]?.message).toContain('当前不存在');
  });

  it('允许引用 Dict 变量的对象路径', () => {
    const diagnostics = buildNodeVariableDiagnostics(
      createNode({
        type: 'browser.fetch',
        targetUrl: 'https://example.com/order/${var.current_row.order_id}',
        selector: '.order-${var.loop_index}::text'
      }),
      ['current_row', 'loop_index']
    );

    expect(diagnostics.inputIssues).toEqual([]);
  });

  it('识别输出覆盖已有变量', () => {
    const diagnostics = buildNodeVariableDiagnostics(
      createNode({ type: 'http.request', responseVariable: 'http_response' }),
      ['http_response']
    );

    expect(diagnostics.outputIssues[0]).toEqual({
      fieldName: 'responseVariable',
      message: '输出变量“http_response”会覆盖已有值',
      severity: 'warn',
      variableName: 'http_response'
    });
  });

  it('能定位被覆盖变量的上游节点', () => {
    const upstreamNode = createNode({ type: 'http.request', responseVariable: 'http_response' });
    upstreamNode.id = 'n0';
    upstreamNode.data.title = 'HTTP 请求';
    const diagnostics = buildNodeVariableDiagnostics(
      createNode({ type: 'data.json.parse', outputVariable: 'http_response' }),
      ['http_response'],
      [upstreamNode, createNode({ type: 'data.json.parse', outputVariable: 'http_response' })]
    );

    expect(diagnostics.outputIssues[0]?.message).toBe('输出变量“http_response”会覆盖上游节点“HTTP 请求”的结果');
    expect(diagnostics.outputIssues[0]?.definedByNodeId).toBe('n0');
  });

  it('允许条件节点引用上游 countVariable 中间变量', () => {
    const detectNode = createNode({
      type: 'browser.extract',
      selector: 'input[type="password"]',
      extractMode: 'count',
      countVariable: 'login_count'
    });
    detectNode.id = 'detect';
    detectNode.data.title = '检测登录表单';
    const conditionNode = createNode({
      type: 'control.condition',
      inputValue: 'login_count > 0'
    });
    conditionNode.id = 'condition';
    conditionNode.data.title = '判断登录态';

    const diagnostics = buildNodeVariableDiagnostics(conditionNode, [], [detectNode, conditionNode]);

    expect(diagnostics.inputIssues).toEqual([]);
  });

  it('为 IO 字段返回状态提示', () => {
    const status = getIoFieldStatus(
      [{ fieldName: 'responseVariable', message: '输出变量“orders”会覆盖已有值', severity: 'warn', variableName: 'orders' }],
      'orders'
    );

    expect(status).toEqual({
      note: '输出变量“orders”会覆盖已有值',
      tone: 'warn'
    });
  });
});
