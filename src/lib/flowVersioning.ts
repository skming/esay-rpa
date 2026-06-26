import type { FlowSavePayload, FlowSnapshot, FlowUpdatePayload } from '../types/electron';
import type { RuntimeVariable } from '../types/rpa';

const FALLBACK_VERSION = 'v1.0.0';

type VersionParts = {
  major: number;
  minor: number;
  patch: number;
};

/** Creates the payload for saving a brand-new flow that hasn't been persisted before. */
export function buildInitialFlowPayload(definition: Record<string, unknown>, inputVariables: RuntimeVariable[], name = '未命名流程'): FlowSavePayload {
  return {
    definition,
    description: '从 Easy RPA 桌面端保存的流程定义',
    inputVariables,
    name,
    status: 'active',
    version: FALLBACK_VERSION
  };
}

/**
 * Computes the next semver patch string for a flow by finding the highest
 * existing version across all flows that share the same name.
 */
export function buildNextFlowVersion(currentFlow: FlowSnapshot, flows: FlowSnapshot[]): string {
  const versions = flows.filter((flow) => flow.name === currentFlow.name).map((flow) => flow.version);
  return formatVersion(incrementPatch(maxVersion([currentFlow.version, ...versions])));
}

export function buildUpdatePayload(
  currentFlow: FlowSnapshot,
  flows: FlowSnapshot[],
  definition: Record<string, unknown>,
  inputVariables: RuntimeVariable[]
): FlowUpdatePayload {
  return {
    definition,
    inputVariables,
    name: currentFlow.name,
    version: buildNextFlowVersion(currentFlow, flows),
    status: 'active',
  };
}

/** Returns true when the canvas definition or input variables differ from the last saved snapshot. */
export function hasDefinitionChanged(currentFlow: FlowSnapshot, definition: Record<string, unknown>, inputVariables: RuntimeVariable[]): boolean {
  return createDefinitionSignature(currentFlow.definition) !== createDefinitionSignature(definition) || stableStringify(currentFlow.inputVariables) !== stableStringify(inputVariables);
}

/**
 * Produces a stable string fingerprint of a flow definition for change detection.
 * `exportedAt` is excluded because it changes on every export regardless of
 * whether the flow logic changed.
 */
export function createDefinitionSignature(definition: Record<string, unknown>): string {
  const stableDefinition = { ...definition };
  delete stableDefinition.exportedAt;
  return stableStringify(stableDefinition);
}

function maxVersion(values: string[]): VersionParts {
  return values.map(parseVersion).reduce((max, value) => (compareVersion(value, max) > 0 ? value : max), parseVersion(FALLBACK_VERSION));
}

function parseVersion(value: string): VersionParts {
  const match = value.trim().match(/^v?(\d+)\.(\d+)\.(\d+)$/);
  if (match === null) {
    return parseVersion(FALLBACK_VERSION);
  }
  return {
    major: Number.parseInt(match[1], 10),
    minor: Number.parseInt(match[2], 10),
    patch: Number.parseInt(match[3], 10)
  };
}

function compareVersion(left: VersionParts, right: VersionParts): number {
  if (left.major !== right.major) return left.major - right.major;
  if (left.minor !== right.minor) return left.minor - right.minor;
  return left.patch - right.patch;
}

function incrementPatch(value: VersionParts): VersionParts {
  return {
    ...value,
    patch: value.patch + 1
  };
}

function formatVersion(value: VersionParts): string {
  return `v${value.major}.${value.minor}.${value.patch}`;
}

/**
 * JSON serialiser with sorted object keys so that key-insertion order does not
 * affect the resulting string, making it safe to use as a change fingerprint.
 */
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
