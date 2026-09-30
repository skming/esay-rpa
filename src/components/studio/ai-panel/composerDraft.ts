import type { Dispatch, SetStateAction } from 'react';

import type { AiAttachment } from './aiPanelTypes';

export type AiComposerDraft = {
  text: string;
  attachments: AiAttachment[];
};

export type AiComposerDrafts = Record<string, AiComposerDraft>;

export type AiComposerDraftSetter = Dispatch<SetStateAction<AiComposerDraft>>;

export function emptyComposerDraft(): AiComposerDraft {
  return { text: '', attachments: [] };
}

export function getComposerDraft(drafts: AiComposerDrafts, sessionKey: string): AiComposerDraft {
  return drafts[sessionKey] ?? emptyComposerDraft();
}

export function updateComposerDrafts(
  drafts: AiComposerDrafts,
  sessionKey: string,
  update: SetStateAction<AiComposerDraft>,
): AiComposerDrafts {
  const current = getComposerDraft(drafts, sessionKey);
  const next = typeof update === 'function' ? update(current) : update;

  if (next.text === '' && next.attachments.length === 0) {
    if (!(sessionKey in drafts)) return drafts;
    const remaining = { ...drafts };
    delete remaining[sessionKey];
    return remaining;
  }

  return { ...drafts, [sessionKey]: next };
}
