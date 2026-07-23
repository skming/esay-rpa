export type FlowDiffType = 'added' | 'changed' | 'removed';
export type FlowDiffScope = 'edge' | 'node';

export type FlowDiffField = {
  key: string;
  label: string;
  before?: string;
  after?: string;
  /** 多行值（脚本、长表达式）需要等宽块渲染，单行值用行内文本即可。 */
  multiline: boolean;
};

export type FlowDiffItem = {
  /** React key，含 scope/type 以避免节点与连线同 id 时相撞。 */
  id: string;
  /** 节点或连线在流程定义里的 id，用于对照画布。 */
  entityId: string;
  scope: FlowDiffScope;
  type: FlowDiffType;
  /** 节点标题，或连线的「起点 → 终点」（尽量用节点标题而不是 id）。 */
  title: string;
  subtitle?: string;
  fields: FlowDiffField[];
};

export type FlowDiffSummary = {
  nodeAdded: number;
  nodeChanged: number;
  nodeRemoved: number;
  edgeAdded: number;
  edgeChanged: number;
  edgeRemoved: number;
  /** 只挪了位置、定义没变的节点数——不计入变更，但要让用户知道差异不是零。 */
  layoutOnly: number;
  items: FlowDiffItem[];
};

type ComparableItem = {
  id: string;
  title: string;
  subtitle?: string;
  /** 参与比对的字段，已剔除易变字段。 */
  fields: Record<string, unknown>;
  signature: string;
};

type DefinitionHolder = { version: string; definition: Record<string, unknown> };

/** 运行态与布局，不属于流程定义；不剔除的话每次保存都会把全部节点报成「变更」。 */
const VOLATILE_FIELDS = new Set(['exportedAt', 'updatedAt', 'position', 'status', 'x', 'y', 'width', 'height', 'measured']);

/** 新增/移除条目的标题行已经展示了这些字段，再列一遍是重复。 */
const SUMMARY_SKIP_FIELDS: Record<FlowDiffScope, Set<string>> = {
  edge: new Set(['id', 'label', 'source', 'target']),
  node: new Set(['id', 'title', 'type', 'kind', 'description'])
};

const FIELD_LABELS: Record<string, string> = {
  adaptive: '自适应',
  anchorText: '锚点文本',
  attribute: '属性',
  code: '脚本代码',
  condition: '条件',
  content: '内容',
  continueOnError: '出错继续',
  countVariable: '计数变量',
  defaultValue: '默认值',
  delayMs: '延时（毫秒）',
  description: '说明',
  expression: '表达式',
  extractMode: '提取方式',
  fallbackSelectors: '备用选择器',
  fetcher: '抓取方式',
  fillMode: '填充方式',
  firstValueVariable: '首值变量',
  force: '强制执行',
  inputValue: '输入值',
  kind: '分类',
  label: '分支标签',
  maxIterations: '最大轮次',
  message: '提示文案',
  outputVariable: '输出变量',
  pageCountVariable: '页数变量',
  path: '路径',
  scope: '作用域',
  selector: '选择器',
  source: '起点',
  target: '终点',
  targetSelector: '目标选择器',
  targetUrl: '目标地址',
  timeoutMs: '超时（毫秒）',
  title: '标题',
  type: '类型',
  value: '值',
  variableName: '变量名'
};

const VALUE_MAX_CHARS = 400;
/** 新增/移除的条目不做逐字段对照，只挑几个能说明它是什么的字段。 */
const SUMMARY_FIELD_LIMIT = 4;

/**
 * @param before 旧版本（历史快照）
 * @param after 新版本（当前流程）
 *
 * 方向固定为「自该历史版本以来发生了什么」：回退按钮撤销的正是这些改动，
 * 反过来算会把用户删掉的节点显示成「新增」。
 */
export function diffFlowSnapshots(before: DefinitionHolder, after: DefinitionHolder): FlowDiffSummary {
  const beforeNodes = readNodes(before.definition);
  const afterNodes = readNodes(after.definition);
  const nodeTitles = buildNodeTitleMap(beforeNodes, afterNodes);

  const nodeItems = diffComparableItems(beforeNodes, afterNodes, 'node');
  const edgeItems = diffComparableItems(readEdges(before.definition, nodeTitles), readEdges(after.definition, nodeTitles), 'edge');

  return {
    edgeAdded: countByType(edgeItems, 'added'),
    edgeChanged: countByType(edgeItems, 'changed'),
    edgeRemoved: countByType(edgeItems, 'removed'),
    items: [...sortByType(nodeItems), ...sortByType(edgeItems)],
    layoutOnly: countLayoutOnlyMoves(before.definition, after.definition),
    nodeAdded: countByType(nodeItems, 'added'),
    nodeChanged: countByType(nodeItems, 'changed'),
    nodeRemoved: countByType(nodeItems, 'removed')
  };
}

function diffComparableItems(beforeItems: ComparableItem[], afterItems: ComparableItem[], scope: FlowDiffScope): FlowDiffItem[] {
  const beforeMap = new Map(beforeItems.map((item) => [item.id, item]));
  const afterMap = new Map(afterItems.map((item) => [item.id, item]));

  // 先按新版本的排列顺序走一遍，再补上只存在于旧版本的条目：
  // 顺序贴近用户在画布上看到的流程走向，比按 id 字典序易读。
  const orderedIds = [...afterItems.map((item) => item.id), ...beforeItems.map((item) => item.id).filter((id) => !afterMap.has(id))];

  return [...new Set(orderedIds)]
    .map((id): FlowDiffItem | null => {
      const before = beforeMap.get(id);
      const after = afterMap.get(id);

      if (before === undefined && after !== undefined) {
        return buildItem(after, scope, 'added', summarizeFields(after.fields, scope, 'after'));
      }
      if (before !== undefined && after === undefined) {
        return buildItem(before, scope, 'removed', summarizeFields(before.fields, scope, 'before'));
      }
      if (before !== undefined && after !== undefined && before.signature !== after.signature) {
        const fields = compareFields(before.fields, after.fields);
        // 字段级比对说了算：签名不同但没有一个字段有实质差异（null 被删掉、描述由缺失变空串），
        // 报成「变更」等于让用户去找一个不存在的改动。
        return fields.length === 0 ? null : buildItem(after, scope, 'changed', fields);
      }
      return null;
    })
    .filter((item): item is FlowDiffItem => item !== null);
}

function buildItem(source: ComparableItem, scope: FlowDiffScope, type: FlowDiffType, fields: FlowDiffField[]): FlowDiffItem {
  return {
    entityId: source.id,
    fields,
    id: `${scope}-${type}-${source.id}`,
    scope,
    subtitle: source.subtitle,
    title: source.title,
    type
  };
}

/** 逐字段对照，只留下真正不同的字段——这是「变更」条目唯一有信息量的部分。 */
function compareFields(before: Record<string, unknown>, after: Record<string, unknown>): FlowDiffField[] {
  const keys = [...new Set([...Object.keys(after), ...Object.keys(before)])].filter((key) => key !== 'id');

  return keys
    .map((key): FlowDiffField | null => {
      const beforeValue = before[key];
      const afterValue = after[key];
      // 空值一律等价：字段被删掉、置为 null、置为空串，对用户是同一件事——没值
      if (isEmptyValue(beforeValue) && isEmptyValue(afterValue)) {
        return null;
      }
      if (stableStringify(beforeValue) === stableStringify(afterValue)) {
        return null;
      }
      const beforeText = isEmptyValue(beforeValue) ? undefined : formatValue(beforeValue);
      const afterText = isEmptyValue(afterValue) ? undefined : formatValue(afterValue);
      return {
        after: afterText,
        before: beforeText,
        key,
        label: FIELD_LABELS[key] ?? key,
        multiline: isMultiline(beforeText) || isMultiline(afterText)
      };
    })
    .filter((field): field is FlowDiffField => field !== null);
}

function summarizeFields(fields: Record<string, unknown>, scope: FlowDiffScope, side: 'after' | 'before'): FlowDiffField[] {
  return Object.entries(fields)
    .filter(([key, value]) => !SUMMARY_SKIP_FIELDS[scope].has(key) && !isEmptyValue(value))
    .slice(0, SUMMARY_FIELD_LIMIT)
    .map(([key, value]): FlowDiffField => {
      const text = formatValue(value);
      return {
        after: side === 'after' ? text : undefined,
        before: side === 'before' ? text : undefined,
        key,
        label: FIELD_LABELS[key] ?? key,
        multiline: isMultiline(text)
      };
    });
}

function readNodes(definition: Record<string, unknown>): ComparableItem[] {
  return readObjectArray(definition, 'nodes').map((node, index) => {
    const id = readString(node.id, `node-${index + 1}`);
    const fields = comparableFields(node);
    return {
      fields,
      id,
      signature: stableStringify(fields),
      subtitle: readString(node.type, readString(node.kind, '')) || undefined,
      title: readString(node.title, id)
    };
  });
}

function readEdges(definition: Record<string, unknown>, nodeTitles: Map<string, string>): ComparableItem[] {
  return readObjectArray(definition, 'edges').map((edge, index) => {
    const source = readString(edge.source, 'unknown');
    const target = readString(edge.target, 'unknown');
    const fields = comparableFields(edge);
    const label = readString(edge.label, '');
    return {
      fields,
      id: readString(edge.id, `${source}-${target}-${index + 1}`),
      signature: stableStringify(fields),
      subtitle: label.length > 0 ? `分支：${label}` : undefined,
      title: `${nodeTitles.get(source) ?? source} → ${nodeTitles.get(target) ?? target}`
    };
  });
}

/** 连线只存节点 id，直接显示会让用户对不上画布；两个版本都收进来，删掉的节点也能显示标题。 */
function buildNodeTitleMap(...groups: ComparableItem[][]): Map<string, string> {
  const titles = new Map<string, string>();
  for (const group of groups) {
    for (const node of group) {
      titles.set(node.id, node.title);
    }
  }
  return titles;
}

function countLayoutOnlyMoves(before: Record<string, unknown>, after: Record<string, unknown>): number {
  const beforeNodes = new Map(readObjectArray(before, 'nodes').map((node, index) => [readString(node.id, `node-${index + 1}`), node]));
  let moved = 0;
  readObjectArray(after, 'nodes').forEach((node, index) => {
    const previous = beforeNodes.get(readString(node.id, `node-${index + 1}`));
    if (previous === undefined) {
      return;
    }
    const sameDefinition = stableStringify(comparableFields(previous)) === stableStringify(comparableFields(node));
    if (sameDefinition && stableStringify(previous.position) !== stableStringify(node.position)) {
      moved += 1;
    }
  });
  return moved;
}

function readObjectArray(definition: Record<string, unknown>, key: 'edges' | 'nodes'): Record<string, unknown>[] {
  const raw = definition[key];
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.filter((item): item is Record<string, unknown> => item !== null && typeof item === 'object' && !Array.isArray(item));
}

function comparableFields(value: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    if (!VOLATILE_FIELDS.has(key) && item !== undefined) {
      result[key] = item;
    }
  }
  return result;
}

function formatValue(value: unknown): string {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, value !== null && typeof value === 'object' ? 2 : undefined) ?? String(value);
  return text.length > VALUE_MAX_CHARS ? `${text.slice(0, VALUE_MAX_CHARS)}…` : text;
}

function isEmptyValue(value: unknown): boolean {
  if (value === undefined || value === null) {
    return true;
  }
  if (typeof value === 'string') {
    return value.trim().length === 0;
  }
  if (Array.isArray(value)) {
    return value.length === 0;
  }
  if (typeof value === 'object') {
    return Object.keys(value).length === 0;
  }
  return false;
}

function isMultiline(text: string | undefined): boolean {
  return text !== undefined && (text.includes('\n') || text.length > 80);
}

function readString(value: unknown, fallback: string): string {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : fallback;
}

function countByType(items: FlowDiffItem[], type: FlowDiffType): number {
  return items.filter((item) => item.type === type).length;
}

const TYPE_ORDER: Record<FlowDiffType, number> = { added: 0, changed: 1, removed: 2 };

function sortByType(items: FlowDiffItem[]): FlowDiffItem[] {
  return [...items].sort((left, right) => TYPE_ORDER[left.type] - TYPE_ORDER[right.type]);
}

function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value) ?? 'undefined';
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableStringify(item)).join(',')}]`;
  }

  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, item]) => item !== undefined)
    .sort(([left], [right]) => left.localeCompare(right));

  return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`).join(',')}}`;
}
