import type { Node } from '@xyflow/react';
import type { ReactElement } from 'react';
import { useEffect } from 'react';

import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import type { RpaNodeConfigDraft, RpaNodeData } from '../../../types/rpa';
import { Field, NumberField, ToggleSwitch } from '../../ui/FormControls';
import { ActionFields } from './ActionFields';
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
      <PanelSection title="操作参数">
        <ActionFields draft={draft} electron={electron} flowTargetUrl={flowTargetUrl} node={node} onDraftPatch={updateDraft} />
        {!isNoExec && (
          <>
            <NumberField
              label="等待超时 (秒)"
              onChange={(event) => updateDraft('timeoutSeconds', Math.max(1, Number.parseInt(event.target.value, 10) || 1))}
              onStep={(delta) => updateDraft('timeoutSeconds', Math.max(1, draft.timeoutSeconds + delta))}
              value={String(draft.timeoutSeconds)}
            />
            <Field label="失败重试次数" onChange={(event) => updateDraft('retryCount', Math.max(0, Number.parseInt(event.target.value, 10) || 0))} type="number" value={String(draft.retryCount)} />
          </>
        )}
      </PanelSection>
      <PanelSection title="记录与行为">
        <ToggleSwitch checked={draft.continueOnError} label="失败时继续执行" onCheckedChange={(checked) => updateDraft('continueOnError', checked)} />
        <ToggleSwitch checked={draft.preScreenshot} label="执行前截图记录" onCheckedChange={(checked) => updateDraft('preScreenshot', checked)} />
        <ToggleSwitch checked={draft.autoSave} label="执行后截图记录" onCheckedChange={(checked) => updateDraft('autoSave', checked)} />
        <ToggleSwitch checked={draft.debugLog} label="输出调试日志" onCheckedChange={(checked) => updateDraft('debugLog', checked)} />
      </PanelSection>
      <PanelSection title="调试">
        <ToggleSwitch checked={draft.breakpoint} label="启用断点" onCheckedChange={(checked) => updateDraft('breakpoint', checked)} />
      </PanelSection>
    </>
  );
}
