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
  status: 'running' | 'done' | 'error' | 'stopped';
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

export interface AiMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  error?: string;
  toolCalls?: ToolCallState[];
  diffPreview?: FlowDiff;
  attachments?: AiAttachment[];
  statusText?: string;
  reasoning?: string;
  processingMs?: number;
  createdAt: number;
}
