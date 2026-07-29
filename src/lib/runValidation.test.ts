import { describe, expect, it } from 'vitest';
import type { Edge, Node } from '@xyflow/react';

import { getBlockingRunIssue, validateFlowConfigurations, validateNodeConfigurationInFlow, validateRunConfiguration } from './runValidation';
import type { RpaNodeData } from '../types/rpa';

function createNode(id: string, overrides: Partial<RpaNodeData> = {}): Node<RpaNodeData> {
  return {
    id,
    position: { x: 0, y: 0 },
    type: id === 'start' || id === 'end' ? 'startEnd' : 'rpaStep',
    data: {
      title: id,
      description: '',
      kind: 'browser',
      status: 'pending',
      ...overrides
    }
  };
}

describe('runValidation', () => {
  it('拦截缺少浏览器采集 URL 的节点', () => {
    const nodes: Node<RpaNodeData>[] = [
      createNode('start'),
      createNode('fetch', {
        title: '采集节点',
        action: {
          type: 'browser.fetch',
          selector: '.item'
        }
      }),
      createNode('end')
    ];
    const edges: Edge[] = [
      { id: 'e1', source: 'start', target: 'fetch' },
      { id: 'e2', source: 'fetch', target: 'end' }
    ];

    const result = validateRunConfiguration(nodes, edges, { scope: 'full' });

    expect(result.primaryIssue).toEqual({
      message: '节点“采集节点”缺少目标网址',
      nodeId: 'fetch',
      severity: 'error'
    });
  });

  it('拦截非法浏览器采集 URL', () => {
    const nodes: Node<RpaNodeData>[] = [
      createNode('start'),
      createNode('fetch', {
        title: '采集节点',
        action: {
          type: 'browser.fetch',
          targetUrl: 'example.com/orders',
          selector: '.item'
        }
      }),
      createNode('end')
    ];
    const edges: Edge[] = [
      { id: 'e1', source: 'start', target: 'fetch' },
      { id: 'e2', source: 'fetch', target: 'end' }
    ];

    const result = validateRunConfiguration(nodes, edges, { scope: 'full' });

    expect(result.primaryIssue).toEqual({
      message: '节点“采集节点”目标网址不是有效 URL',
      nodeId: 'fetch',
      severity: 'error'
    });
  });

  it('允许 URL 使用运行时变量模板', () => {
    const nodes: Node<RpaNodeData>[] = [
      createNode('start'),
      createNode('fetch', {
        title: '采集节点',
        action: {
          type: 'browser.fetch',
          targetUrl: '${var.target_url}',
          selector: '.item'
        }
      }),
      createNode('end')
    ];
    const edges: Edge[] = [
      { id: 'e1', source: 'start', target: 'fetch' },
      { id: 'e2', source: 'fetch', target: 'end' }
    ];

    const result = validateRunConfiguration(nodes, edges, {
      availableVariableNames: ['target_url'],
      scope: 'full'
    });

    expect(getBlockingRunIssue(result)).toBeNull();
  });

  it('拦截非法超时配置', () => {
    const nodes: Node<RpaNodeData>[] = [
      createNode('start'),
      createNode('fetch', {
        title: '采集节点',
        action: {
          type: 'browser.fetch',
          targetUrl: 'https://example.com/orders',
          selector: '.item',
          timeoutMs: 0
        }
      }),
      createNode('end')
    ];
    const edges: Edge[] = [
      { id: 'e1', source: 'start', target: 'fetch' },
      { id: 'e2', source: 'fetch', target: 'end' }
    ];

    const result = validateRunConfiguration(nodes, edges, { scope: 'full' });

    expect(result.primaryIssue).toEqual({
      message: '节点“采集节点”超时必须大于 0 ms',
      nodeId: 'fetch',
      severity: 'error'
    });
  });

  it('拦截缺少脚本路径的脚本节点', () => {
    const nodes: Node<RpaNodeData>[] = [
      createNode('start'),
      createNode('script', {
        title: '清洗脚本',
        kind: 'script',
        action: {
          type: 'script.python'
        }
      }),
      createNode('end')
    ];
    const edges: Edge[] = [
      { id: 'e1', source: 'start', target: 'script' },
      { id: 'e2', source: 'script', target: 'end' }
    ];

    const result = validateRunConfiguration(nodes, edges, { scope: 'full' });

    expect(result.primaryIssue?.nodeId).toBe('script');
    expect(result.primaryIssue?.message).toContain('脚本路径');
  });

  it('从选中节点运行时，如果范围内没有可执行动作则阻止启动', () => {
    const nodes: Node<RpaNodeData>[] = [
      createNode('start'),
      createNode('branch', {
        title: '条件判断',
        kind: 'control',
        action: {
          type: 'control.condition',
          inputValue: 'row_count > 0'
        }
      }),
      createNode('end')
    ];
    const edges: Edge[] = [
      { id: 'e1', source: 'start', target: 'branch' },
      { id: 'e2', source: 'branch', target: 'end' }
    ];

    const result = validateRunConfiguration(nodes, edges, {
      scope: 'selected-only',
      startNodeId: 'end'
    });

    expect(result.primaryIssue).toEqual({
      message: '当前运行范围内没有可执行动作节点',
      nodeId: 'end',
      severity: 'error'
    });
  });

  it('能识别引用了上游未定义变量的节点', () => {
    const nodes: Node<RpaNodeData>[] = [
      createNode('start'),
      createNode('fill', {
        title: '输入账号',
        action: {
          type: 'browser.fill',
          selector: '#username',
          inputValue: '${var.missing_username}'
        }
      }),
      createNode('end')
    ];
    const edges: Edge[] = [
      { id: 'e1', source: 'start', target: 'fill' },
      { id: 'e2', source: 'fill', target: 'end' }
    ];

    const result = validateRunConfiguration(nodes, edges, {
      availableVariableNames: ['known_value'],
      scope: 'full'
    });

    expect(result.primaryIssue?.nodeId).toBe('fill');
    expect(result.primaryIssue?.message).toContain('missing_username');
  });

  it('允许引用上游 Dict 变量的对象路径', () => {
    const nodes: Node<RpaNodeData>[] = [
      createNode('start'),
      createNode('loop', {
        title: '遍历订单',
        kind: 'control',
        action: {
          type: 'control.foreach',
          itemsVariable: 'excel_rows',
          itemVariable: 'current_row',
          indexVariable: 'loop_index'
        }
      }),
      createNode('fetch', {
        title: '采集详情',
        action: {
          type: 'browser.fetch',
          targetUrl: 'https://example.com/order/${var.current_row.order_id}',
          selector: '.order-${var.loop_index}::text'
        }
      }),
      createNode('end')
    ];
    const edges: Edge[] = [
      { id: 'e1', source: 'start', target: 'loop' },
      { id: 'e2', source: 'loop', target: 'fetch', label: '循环体' },
      { id: 'e3', source: 'fetch', target: 'loop' },
      { id: 'e4', source: 'loop', target: 'end', label: '完成' }
    ];

    const result = validateRunConfiguration(nodes, edges, {
      availableVariableNames: ['excel_rows'],
      scope: 'full'
    });

    expect(getBlockingRunIssue(result)).toBeNull();
  });

  it('提示开始节点不可达的孤立节点', () => {
    const nodes: Node<RpaNodeData>[] = [
      createNode('start'),
      createNode('fetch', {
        title: '采集列表',
        action: {
          type: 'browser.fetch',
          targetUrl: 'https://example.com',
          selector: '.item'
        }
      }),
      createNode('isolated', {
        title: '孤立节点',
        action: {
          type: 'http.request',
          url: 'https://api.example.com'
        }
      }),
      createNode('end')
    ];
    const edges: Edge[] = [
      { id: 'e1', source: 'start', target: 'fetch' },
      { id: 'e2', source: 'fetch', target: 'end' }
    ];

    const result = validateRunConfiguration(nodes, edges, { scope: 'full' });

    expect(result.issues.some((issue) => issue.nodeId === 'isolated' && issue.message.includes('不可达'))).toBe(true);
    expect(getBlockingRunIssue(result)).toBeNull();
  });

  it('拦截缺少循环体连线的遍历节点', () => {
    const nodes: Node<RpaNodeData>[] = [
      createNode('start'),
      createNode('loop', {
        title: '遍历订单',
        kind: 'control',
        action: {
          type: 'control.foreach',
          itemsVariable: 'excel_rows',
          itemVariable: 'current_row'
        }
      }),
      createNode('end')
    ];
    const edges: Edge[] = [
      { id: 'e1', source: 'start', target: 'loop' },
      { id: 'e2', source: 'loop', target: 'end', label: 'exit' }
    ];

    const result = validateRunConfiguration(nodes, edges, {
      availableVariableNames: ['excel_rows'],
      scope: 'full'
    });

    expect(result.primaryIssue).toEqual({
      message: '节点“遍历订单”缺少循环体连线',
      nodeId: 'loop',
      severity: 'error'
    });
  });

  it('提示条件节点缺少完整分支', () => {
    const nodes: Node<RpaNodeData>[] = [
      createNode('start'),
      createNode('condition', {
        title: '判断是否继续',
        kind: 'control',
        action: {
          type: 'control.condition',
          inputValue: 'row_count > 0'
        }
      }),
      createNode('end')
    ];
    const edges: Edge[] = [
      { id: 'e1', source: 'start', target: 'condition' },
      { id: 'e2', source: 'condition', target: 'end', label: 'true' }
    ];

    const result = validateRunConfiguration(nodes, edges, {
      availableVariableNames: ['row_count'],
      scope: 'full'
    });

    expect(result.issues.some((issue) => issue.nodeId === 'condition' && issue.message.includes('单条分支'))).toBe(true);
  });

  it('阻止条件表达式使用模板变量语法', () => {
    const nodes: Node<RpaNodeData>[] = [
      createNode('start'),
      createNode('condition', {
        title: '判断登录态',
        kind: 'control',
        action: {
          type: 'control.condition',
          inputValue: '${var.login_count} > 0'
        }
      }),
      createNode('end')
    ];
    const edges: Edge[] = [
      { id: 'e1', source: 'start', target: 'condition' },
      { id: 'e2', source: 'condition', target: 'end', label: 'true' },
      { id: 'e3', source: 'condition', target: 'end', label: 'false' }
    ];

    const result = validateRunConfiguration(nodes, edges, {
      availableVariableNames: ['login_count'],
      scope: 'full'
    });

    expect(result.issues.some((issue) => issue.nodeId === 'condition' && issue.message.includes('裸变量名'))).toBe(true);
  });
  // validateFlowConfigurations 是画布徽标用的批量版，逐节点跑单节点版会是 O(n²)；
  // 两者必须给出同样的结果，否则批量优化会改变画布上的告警
  it('批量校验与逐节点校验结果一致', () => {
    const nodes: Node<RpaNodeData>[] = [
      createNode('start'),
      createNode('fetch', {
        title: '采集节点',
        action: { type: 'browser.fetch', selector: '.item' }
      }),
      createNode('fill', {
        title: '填写节点',
        action: { type: 'browser.fill', selector: '#kw', inputValue: '${var.keyword}' }
      }),
      createNode('orphan', {
        title: '孤儿节点',
        action: { type: 'variable.log', inputValue: 'hi' }
      }),
      createNode('end')
    ];
    const edges: Edge[] = [
      { id: 'e1', source: 'start', target: 'fetch' },
      { id: 'e2', source: 'fetch', target: 'fill' },
      { id: 'e3', source: 'fill', target: 'end' }
    ];

    const batch = validateFlowConfigurations(nodes, edges, []);

    for (const node of nodes) {
      expect(batch.get(node.id) ?? []).toEqual(validateNodeConfigurationInFlow(node, nodes, edges, []));
    }
    expect((batch.get('fetch') ?? []).length).toBeGreaterThan(0);
    expect((batch.get('fill') ?? []).length).toBeGreaterThan(0);
  });

  it('URL 式翻页节点不再要求下一页按钮选择器', () => {
    const node = createNode('paginate', {
      title: '翻页抓取',
      action: {
        type: 'browser.paginateNext',
        urlTemplate: 'https://example.com/list?p=${page}',
        targetSelector: '.row::text',
        responseVariable: 'rows'
      }
    });

    const issues = validateNodeConfigurationInFlow(node, [node], [], []).filter((issue) => issue.severity === 'error');

    expect(issues).toEqual([]);
  });

  it('翻页地址模板缺少 ${page} 时拦截：每页请求的会是同一个地址', () => {
    const node = createNode('paginate', {
      title: '翻页抓取',
      action: {
        type: 'browser.paginateNext',
        urlTemplate: 'https://example.com/list',
        targetSelector: '.row::text',
        responseVariable: 'rows'
      }
    });

    const issues = validateNodeConfigurationInFlow(node, [node], [], []).filter((issue) => issue.severity === 'error');

    expect(issues).toHaveLength(1);
    expect(issues[0]?.severity).toBe('error');
    expect(issues[0]?.message).toContain('${page}');
  });
});
