import type { Node } from '@xyflow/react';

import type { ElectronBridgeState } from '../../../../hooks/useElectronBridge';
import type { RpaNodeConfigDraft, RpaNodeData } from '../../../../types/rpa';

export type ActionDraftPatch = <K extends keyof RpaNodeConfigDraft>(key: K, value: RpaNodeConfigDraft[K]) => void;

export type ActionFieldsProps = {
  draft: RpaNodeConfigDraft;
  electron: ElectronBridgeState;
  /** 当前节点自身无 targetUrl 时，由流程中 browser.open 节点提供的回退 URL */
  flowTargetUrl?: string;
  node: Node<RpaNodeData>;
  onDraftPatch: ActionDraftPatch;
};
