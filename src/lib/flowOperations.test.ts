import type { Edge, Node } from '@xyflow/react';
import { describe, expect, it } from 'vitest';

import { deleteNodeAndReconnect, summarizeDeleteImpact } from './flowOperations';
import type { RpaNodeData } from '../types/rpa';

function node(id: string, title = id): Node<RpaNodeData> {
  return { id, position: { x: 0, y: 0 }, data: { title } as RpaNodeData };
}

function edge(source: string, target: string, label?: string): Edge {
  return { id: `${source}-${target}`, source, target, ...(label === undefined ? {} : { label }) };
}

/** 影响面提示的唯一价值在于「和真删法一致」：一旦两者分叉，提示会替用户保住错的那条分支。 */
describe('summarizeDeleteImpact', () => {
  const linear = {
    nodes: [node('start'), node('a', '打开网页'), node('end')],
    edges: [edge('start', 'a'), edge('a', 'end')],
  };

  it('单进单出是干净的接续，不该报任何影响', () => {
    expect(summarizeDeleteImpact(linear.nodes, linear.edges, 'a')).toEqual({
      reconnects: true,
      droppedEdgeCount: 0,
      droppedNeighborTitles: [],
    });
  });

  it('多出边的循环节点：只重连第一条，其余分支要如实报出来', () => {
    const nodes = [node('start'), node('loop', '循环每一行'), node('body', '提取表格'), node('end', '结束')];
    const edges = [
      edge('start', 'loop'),
      edge('loop', 'body', '循环体'),
      edge('loop', 'end', '结束'),
      edge('body', 'loop'),
    ];
    const impact = summarizeDeleteImpact(nodes, edges, 'loop');

    // 入边两条（start、body）、出边两条（body、end），各保一条，剩下两条丢弃
    expect(impact.reconnects).toBe(true);
    expect(impact.droppedEdgeCount).toBe(2);
    expect(impact.droppedNeighborTitles).toEqual(['提取表格', '结束']);

    // 与真实删法对账：报出来的丢弃条数必须等于实际少掉的连线数
    const remaining = deleteNodeAndReconnect(nodes, edges, 'loop');
    const untouched = edges.filter((e) => e.source !== 'loop' && e.target !== 'loop').length;
    expect(remaining.length).toBe(untouched + 1);
    expect(edges.length - remaining.length).toBe(impact.droppedEdgeCount + 1);
  });

  it('缺出边时接不上，必须说流程会断开而不是承诺自动重连', () => {
    const nodes = [node('start'), node('a', '导出 CSV')];
    const edges = [edge('start', 'a')];
    expect(summarizeDeleteImpact(nodes, edges, 'a')).toEqual({
      reconnects: false,
      droppedEdgeCount: 1,
      droppedNeighborTitles: ['start'],
    });
    // deleteNodeAndReconnect 此时只做删除，确实没有新边
    expect(deleteNodeAndReconnect(nodes, edges, 'a')).toEqual([]);
  });

  it('前后是同一个节点（两点互连）时同样接不上', () => {
    const nodes = [node('a'), node('b')];
    const edges = [edge('a', 'b'), edge('b', 'a')];
    const impact = summarizeDeleteImpact(nodes, edges, 'b');
    expect(impact.reconnects).toBe(false);
    expect(impact.droppedEdgeCount).toBe(2);
    expect(deleteNodeAndReconnect(nodes, edges, 'b')).toEqual([]);
  });

  it('start/end 与不存在的节点删不掉，不该报影响', () => {
    for (const id of ['start', 'end', 'ghost']) {
      expect(summarizeDeleteImpact(linear.nodes, linear.edges, id)).toEqual({
        reconnects: true,
        droppedEdgeCount: 0,
        droppedNeighborTitles: [],
      });
    }
  });

  it('同名邻居的标题去重，但条数不受影响', () => {
    const nodes = [node('start'), node('a', '分叉'), node('b1', '点击'), node('b2', '点击'), node('b3', '点击')];
    const edges = [edge('start', 'a'), edge('a', 'b1'), edge('a', 'b2'), edge('a', 'b3')];
    const impact = summarizeDeleteImpact(nodes, edges, 'a');
    expect(impact.droppedEdgeCount).toBe(2);
    expect(impact.droppedNeighborTitles).toEqual(['点击']);
  });
});
