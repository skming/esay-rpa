export type FlowDiffType = 'added' | 'changed' | 'removed';

/** A single node or edge change between two flow versions. */
export type FlowDiffItem = {
  id: string;
  title: string;
  before?: string;
  after?: string;
  type: FlowDiffType;
};

/** Aggregated counts and item list produced by `diffFlowSnapshots`. */
export type FlowDiffSummary = {
  nodeAdded: number;
  nodeChanged: number;
  nodeRemoved: number;
  edgeAdded: number;
  edgeChanged: number;
  edgeRemoved: number;
  items: FlowDiffItem[];
};

/** Internal value type used for stable ID-keyed comparison. */
type ComparableItem = {
  id: string;
  label: string;
  /** Sorted JSON fingerprint of the item excluding volatile fields (exportedAt, updatedAt). */
  signature: string;
};

type DefinitionHolder = { version: string; definition: Record<string, unknown> };

/** Computes a structural diff between two flow snapshots for display in the version history dialog. */
export function diffFlowSnapshots(base: DefinitionHolder, target: DefinitionHolder): FlowDiffSummary {
  const nodeItems = diffComparableItems(readNodes(base.definition), readNodes(target.definition), '节点');
  const edgeItems = diffComparableItems(readEdges(base.definition), readEdges(target.definition), '连线');

  return {
    edgeAdded: countByType(edgeItems, 'added'),
    edgeChanged: countByType(edgeItems, 'changed'),
    edgeRemoved: countByType(edgeItems, 'removed'),
    items: [...nodeItems, ...edgeItems],
    nodeAdded: countByType(nodeItems, 'added'),
    nodeChanged: countByType(nodeItems, 'changed'),
    nodeRemoved: countByType(nodeItems, 'removed')
  };
}

function diffComparableItems(baseItems: ComparableItem[], targetItems: ComparableItem[], prefix: string): FlowDiffItem[] {
  const baseMap = new Map(baseItems.map((item) => [item.id, item]));
  const targetMap = new Map(targetItems.map((item) => [item.id, item]));
  const ids = [...new Set([...baseMap.keys(), ...targetMap.keys()])].sort((left, right) => left.localeCompare(right));

  return ids
    .map((id): FlowDiffItem | null => {
      const before = baseMap.get(id);
      const after = targetMap.get(id);
      if (before === undefined && after !== undefined) {
        return {
          id: `${prefix}-${id}-added`,
          after: after.label,
          title: `${prefix}新增 · ${after.label}`,
          type: 'added' as const
        };
      }
      if (before !== undefined && after === undefined) {
        return {
          id: `${prefix}-${id}-removed`,
          before: before.label,
          title: `${prefix}移除 · ${before.label}`,
          type: 'removed' as const
        };
      }
      if (before !== undefined && after !== undefined && before.signature !== after.signature) {
        return {
          id: `${prefix}-${id}-changed`,
          after: after.label,
          before: before.label,
          title: `${prefix}变更 · ${after.label}`,
          type: 'changed' as const
        };
      }
      return null;
    })
    .filter((item): item is FlowDiffItem => item !== null);
}

function readNodes(definition: Record<string, unknown>): ComparableItem[] {
  const rawNodes = definition.nodes;
  if (!Array.isArray(rawNodes)) {
    return [];
  }
  return rawNodes
    .filter((node): node is Record<string, unknown> => node !== null && typeof node === 'object' && !Array.isArray(node))
    .map((node, index) => {
      const id = readString(node.id, `node-${index + 1}`);
      const title = readString(node.title, id);
      const type = readString(node.type, readString(node.kind, 'step'));
      const selector = readString(node.selector, '');
      return {
        id,
        label: selector.length > 0 ? `${title} · ${selector}` : `${title} · ${type}`,
        signature: stableStringify(stripVolatileFields(node))
      };
    });
}

function readEdges(definition: Record<string, unknown>): ComparableItem[] {
  const rawEdges = definition.edges;
  if (!Array.isArray(rawEdges)) {
    return [];
  }
  return rawEdges
    .filter((edge): edge is Record<string, unknown> => edge !== null && typeof edge === 'object' && !Array.isArray(edge))
    .map((edge, index) => {
      const source = readString(edge.source, 'unknown');
      const target = readString(edge.target, 'unknown');
      const id = readString(edge.id, `${source}-${target}-${index + 1}`);
      return {
        id,
        label: `${source} → ${target}`,
        signature: stableStringify(stripVolatileFields(edge))
      };
    });
}

function stripVolatileFields(value: Record<string, unknown>): Record<string, unknown> {
  const stableValue = { ...value };
  delete stableValue.exportedAt;
  delete stableValue.updatedAt;
  return stableValue;
}

function readString(value: unknown, fallback: string): string {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : fallback;
}

function countByType(items: FlowDiffItem[], type: FlowDiffType): number {
  return items.filter((item) => item.type === type).length;
}

function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableStringify(item)).join(',')}]`;
  }

  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, item]) => item !== undefined)
    .sort(([left], [right]) => left.localeCompare(right));

  return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`).join(',')}}`;
}
