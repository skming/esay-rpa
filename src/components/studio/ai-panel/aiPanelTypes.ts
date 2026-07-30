export interface AiAttachment {
  id: string;
  type: 'image' | 'file';
  name: string;
  dataUrl: string;
  mimeType: string;
  size: number;
}

export interface ToolCallState {
  id: string;
  tool: string;
  args: string;
  result?: unknown;
  status: 'running' | 'done' | 'error' | 'stopped' | 'blocked';
}

export interface FlowDiff {
  flow_id: string;
  add_nodes?: unknown[];
  update_nodes?: { id: string; patch: Record<string, unknown> }[];
  remove_node_ids?: string[];
  add_edges?: unknown[];
  remove_edge_ids?: string[];
}

export interface NodeLookupItem {
  id: string;
  title?: string;
  type?: string;
}

/** 一轮对话累计的用量，由后端 usage 事件推送（每轮刷新一次，值是累计值不是增量）。 */
export interface AiUsage {
  rounds: number;
  max_rounds: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  total_tokens: number;
  tool_calls: number;
  blocked_calls: number;
  llm_seconds: number;
}

export type VerificationStatus = 'modified_unverified' | 'run_verified' | 'accepted';

export interface AiMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  error?: string;
  toolCalls?: ToolCallState[];
  diffPreview?: FlowDiff;
  attachments?: AiAttachment[];
  statusText?: string;
  /** 工具执行途中的进度补充（已用时、步数），由 heartbeat 刷新 */
  statusDetail?: string;
  reasoning?: string;
  processingMs?: number;
  usage?: AiUsage;
  /** 当前流程 revision 的证据等级，由后端证据账本确定，不能由回复文案推断。 */
  verificationStatus?: VerificationStatus;
  verificationRevision?: number;
  createdAt: number;
  finishedAt?: number;
}
