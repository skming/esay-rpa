import { MarkerType, type Edge, type Node } from '@xyflow/react';
import {
  Braces,
  Code2,
  Database,
  FileText,
  Globe2,
  MousePointer2,
  Terminal,
  Workflow
} from 'lucide-react';

import type { ComponentGroup, KindStyle, NodeKind, RpaNodeData } from '../types/rpa';

export const kindStyles: Record<NodeKind, KindStyle> = {
  // 蓝色而非靛蓝：靛蓝专留给 running 状态，避免画布上出现两种含义的靛蓝
  browser: {
    accent: '#2563eb',
    bg: '#eff6ff',
    border: '#dbeafe',
    pill: '#dbeafe',
    text: '#1d4ed8',
    icon: Globe2,
    label: '浏览器操作'
  },
  excel: {
    accent: '#16a34a',
    bg: '#f0fdf4',
    border: '#a7f3d0',
    pill: '#dcfce7',
    text: '#15803d',
    icon: Database,
    label: 'Excel / WPS'
  },
  ui: {
    accent: '#7c3aed',
    bg: '#faf5ff',
    border: '#fbcfe8',
    pill: '#ede9fe',
    text: '#6d28d9',
    icon: MousePointer2,
    label: '界面自动化'
  },
  file: {
    accent: '#ea580c',
    bg: '#fff7ed',
    border: '#fed7aa',
    pill: '#ffedd5',
    text: '#c2410c',
    icon: FileText,
    label: '文件 / 目录'
  },
  data: {
    accent: '#0891b2',
    bg: '#ecfeff',
    border: '#a5f3fc',
    pill: '#cffafe',
    text: '#0e7490',
    icon: Braces,
    label: '数据处理'
  },
  script: {
    accent: '#475569',
    bg: '#f8fafc',
    border: '#e2e8f0',
    pill: '#f1f5f9',
    text: '#1e293b',
    icon: Code2,
    label: '代码 / 脚本'
  },
  control: {
    accent: '#dc2626',
    bg: '#fff1f2',
    border: '#fecaca',
    pill: '#ffe4e6',
    text: '#b91c1c',
    icon: Workflow,
    label: '流程控制'
  },
  variable: {
    accent: '#4f46e5',
    bg: '#eef2ff',
    border: '#c7d2fe',
    pill: '#e0e7ff',
    text: '#4338ca',
    icon: Terminal,
    label: '变量 / 消息'
  }
};

export const componentGroups: ComponentGroup[] = [
  {
    id: 'browser',
    label: '浏览器操作',
    icon: Globe2,
    items: [
      { label: '打开网页', popular: true },
      { label: '确保已登录' },
      { label: '点击元素', popular: true },
      { label: '输入文本', popular: true },
      { label: '获取文本' },
      { label: '页面截图' },
      { label: '等待元素' },
      { label: '滚动页面' },
      { label: '悬停元素' },
      { label: '切换标签页' },
      { label: '关闭标签页' }
    ]
  },
  {
    id: 'excel',
    label: 'Excel / WPS',
    icon: Database,
    items: [
      { label: '打开工作簿', popular: true },
      { label: '读取单元格', popular: true },
      { label: '写入单元格', popular: true },
      { label: '新增数据行' },
      { label: '删除数据行' },
      { label: '保存文件' },
      { label: '获取行数' },
      { label: '筛选/排序' },
      { label: '导出 CSV' }
    ]
  },
  {
    id: 'ui',
    label: '界面自动化',
    icon: MousePointer2,
    items: [
      { label: '点击控件', popular: true },
      { label: '输入文字', popular: true },
      { label: '获取属性' },
      { label: '等待控件' },
      { label: '截图控件' },
      { label: '列表操作' },
      { label: '下拉选择' },
      { label: '复选框' },
      { label: '拖拽操作' }
    ]
  },
  {
    id: 'file',
    label: '文件 / 目录',
    icon: FileText,
    items: [
      { label: '读取文件' },
      { label: '写入文件' },
      { label: '复制/移动' },
      { label: '删除文件' },
      { label: '遍历文件夹', popular: true },
      { label: '压缩解压' },
      { label: '重命名' },
      { label: '监听变化' }
    ]
  },
  {
    id: 'data',
    label: '数据处理',
    icon: Braces,
    items: [
      { label: '数据表操作', popular: true },
      { label: '字符串处理', popular: true },
      { label: '正则匹配' },
      { label: 'JSON 解析' },
      { label: '列表处理' },
      { label: '数字运算' },
      { label: '类型转换' },
      { label: '加密解密' }
    ]
  },
  {
    id: 'script',
    label: '代码 / 脚本',
    icon: Code2,
    items: [
      { label: '执行 Python', popular: true },
      { label: 'HTTP 请求', popular: true },
      { label: '执行 JavaScript' },
      { label: '执行 Shell' },
      { label: '调用 API' },
      { label: 'WebSocket' }
    ]
  },
  {
    id: 'control',
    label: '流程控制',
    icon: Workflow,
    items: [
      { label: '条件判断', popular: true },
      { label: '循环', popular: true },
      { label: '遍历列表', popular: true },
      { label: '遍历数据表', popular: true },
      { label: '重复直到', popular: true },
      { label: '中断循环' },
      { label: '等待延时' },
      { label: '子流程' },
      { label: '异常处理' },
      { label: '重试机制' },
      { label: '人工接管' }
    ]
  },
  {
    id: 'variable',
    label: '变量 / 消息',
    icon: Terminal,
    items: [
      { label: '赋值变量' },
      { label: '获取变量' },
      { label: '输入弹窗' },
      { label: '输出日志', popular: true },
      { label: '消息通知' },
      { label: '剪贴板' }
    ]
  }
];

export const totalComponents = componentGroups.reduce((sum, group) => sum + group.items.length, 0);

// 顺序固定为 [start, end]：ensureStartEndNodes 按下标 0/1 补齐起止节点，调序会取错
export const initialNodes: Node<RpaNodeData>[] = [
  {
    id: 'start',
    type: 'startEnd',
    position: { x: 560, y: 20 },
    data: { title: '开始', description: 'manual trigger', kind: 'control', status: 'pending' }
  },
  {
    id: 'end',
    type: 'startEnd',
    position: { x: 560, y: 160 },
    data: { title: '结束', description: 'done', kind: 'control', status: 'pending' }
  }
];

const defaultEdgeStyle = { stroke: '#94a3b8', strokeWidth: 1.5 };
const defaultMarker = { type: MarkerType.ArrowClosed, color: '#94a3b8' };

export const initialEdges: Edge[] = [
  { id: 'e-start-end', source: 'start', target: 'end', type: 'smoothstep', markerEnd: defaultMarker, style: defaultEdgeStyle }
];
