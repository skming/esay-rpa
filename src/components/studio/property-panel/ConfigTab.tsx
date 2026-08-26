import type { Node } from '@xyflow/react';
import type { ReactElement } from 'react';
import { useEffect, useEffectEvent } from 'react';

import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import type { PickerResult } from '../../../types/electron';
import { DEFAULT_ACTION_TYPE_BY_KIND, type RpaNodeConfigDraft, type RpaNodeData } from '../../../types/rpa';
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

  const actionType = node.data.action?.type ?? DEFAULT_ACTION_TYPE_BY_KIND[node.data.kind];
  const isNoExec = actionType === 'control.noop' || actionType === 'control.break';

  // 拾取结果回填草稿要读到最新 draft，但不能因 draft 变化重跑：否则用户手改过的 selector
  // 会被上一次的拾取结果盖回去。useEffectEvent 就是这个语义——闭包读最新值，自身不进依赖。
  const applyPickerResult = useEffectEvent((result: PickerResult): void => {
    onDraftChange({
      ...draft,
      selector: result.selector,
      targetUrl: result.url
    });
  });

  useEffect(() => {
    if (electron.lastPickerResult === null || node.data.kind !== 'browser') {
      return;
    }
    applyPickerResult(electron.lastPickerResult);
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
