import type { RunEvent } from '../src/types/electron';
import type { NodeStatus, RunLogLevel, RuntimeStatus } from '../src/types/rpa';

export type BackendNodeState = {
  runId: string;
  knownNodeIds: Set<string> | null;
  lastActiveNodeId: string | null;
  nodeStates: Map<string, NodeStatus>;
};

export function normalizeRuntimeStatus(status: string): RuntimeStatus;
export function normalizeLogLevel(level: string): RunLogLevel;
export function resolveBackendLogNodeId(log: { nodeId?: string | null; message?: string }, lastActiveNodeId?: string | null): string;
export function collectKnownNodeIds(flowDefinition?: Record<string, unknown>): Set<string> | null;
export function readBackendTotalSteps(progress?: { totalStep?: number; totalSteps?: number }): number;
export function formatLogTime(date: Date): string;
export function applyBackendNodeState(run: BackendNodeState, emit: (event: RunEvent) => void, nodeId: string, log: { level: string }): void;
export function finalizeLastActiveNode(run: BackendNodeState, emit: (event: RunEvent) => void, status: NodeStatus, exceptNodeId?: string): void;
