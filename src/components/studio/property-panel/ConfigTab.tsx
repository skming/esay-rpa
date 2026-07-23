import type { Node } from '@xyflow/react';
import type { ReactElement } from 'react';
import { useEffect } from 'react';

import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import type { RpaNodeConfigDraft, RpaNodeData } from '../../../types/rpa';
import { Field } from '../../ui/FormControls';
import { ActionFields } from './ActionFields';
import { ExecutionStrategySection } from './ExecutionStrategySection';
import { PanelSection } from './PanelSection';

export function ConfigTab({
  draft,
  electron,
  flowTargetUrl,
  node,
  onDraftChange
}: {
  draft: RpaNodeConfigDraft;
  electron: ElectronBridgeState;
  flowTargetUrl?: string;
  node: Node<RpaNodeData>;
  onDraftChange: (draft: RpaNodeConfigDraft) => void;
}): ReactElement {
  const updateDraft = <K extends keyof RpaNodeConfigDraft>(key: K, value: RpaNodeConfigDraft[K]): void => {
    onDraftChange({ ...draft, [key]: value });
  };

  const actionType = node.data.action?.type ?? `${node.data.kind}.step`;
  const isNoExec = actionType === 'control.noop' || actionType === 'control.break';

  // 故意不依赖 draft/onDraftChange：加进去会在拾取结果没变时因 draft 变化重复写回
  useEffect(() => {
    if (electron.lastPickerResult === null || node.data.kind !== 'browser') {
      return;
    }
    onDraftChange({
      ...draft,
      selector: electron.lastPickerResult.selector,
      targetUrl: electron.lastPickerResult.url
    });
  }, [electron.lastPickerResult, node.data.kind]);

  return (
    <>
      <PanelSection title="基本信息">
        <Field label="步骤名称" onChange={(event) => updateDraft('title', event.target.value)} value={draft.title} />
        <Field label="步骤描述" onChange={(event) => updateDraft('description', event.target.value)} placeholder="添加备注..." value={draft.description} />
      </PanelSection>
      <ExecutionStrategySection actionType={actionType} draft={draft} isNoExec={isNoExec} onDraftPatch={updateDraft} />
      <PanelSection title="操作参数">
        <ActionFields draft={draft} electron={electron} flowTargetUrl={flowTargetUrl} node={node} onDraftPatch={updateDraft} />
      </PanelSection>
    </>
  );
}
