import type { ReactElement } from 'react';

import { BrowserActionFields } from './action-fields/BrowserActionFields';
import { ControlActionFields } from './action-fields/ControlActionFields';
import { DataActionFields } from './action-fields/DataActionFields';
import { FileActionFields } from './action-fields/FileActionFields';
import { HttpActionFields } from './action-fields/HttpActionFields';
import { ScriptActionFields } from './action-fields/ScriptActionFields';
import { VariableActionFields } from './action-fields/VariableActionFields';
import type { ActionFieldsProps } from './action-fields/types';
import { DEFAULT_ACTION_TYPE_BY_KIND } from '../../../types/rpa';

export function ActionFields({ draft, electron, flowTargetUrl, node, onDraftPatch }: ActionFieldsProps): ReactElement {
  const actionType = node.data.action?.type ?? DEFAULT_ACTION_TYPE_BY_KIND[node.data.kind];
  if (actionType === 'http.request') {
    return <HttpActionFields draft={draft} electron={electron} onDraftPatch={onDraftPatch} />;
  }
  if (actionType.startsWith('script.')) {
    return <ScriptActionFields draft={draft} electron={electron} node={node} onDraftPatch={onDraftPatch} />;
  }
  if (actionType.startsWith('excel.') || actionType.startsWith('file.')) {
    return <FileActionFields draft={draft} electron={electron} node={node} onDraftPatch={onDraftPatch} />;
  }
  if (actionType.startsWith('data.')) {
    return <DataActionFields draft={draft} electron={electron} node={node} onDraftPatch={onDraftPatch} />;
  }
  if (actionType.startsWith('control.')) {
    return <ControlActionFields draft={draft} electron={electron} node={node} onDraftPatch={onDraftPatch} />;
  }
  if (actionType.startsWith('variable.')) {
    return <VariableActionFields draft={draft} electron={electron} node={node} onDraftPatch={onDraftPatch} />;
  }
  return <BrowserActionFields draft={draft} electron={electron} flowTargetUrl={flowTargetUrl} node={node} onDraftPatch={onDraftPatch} />;
}
