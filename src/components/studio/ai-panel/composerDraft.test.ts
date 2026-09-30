import { describe, expect, it } from 'vitest';

import type { AiAttachment } from './aiPanelTypes';
import { getComposerDraft, updateComposerDrafts } from './composerDraft';

const image: AiAttachment = {
  id: 'image-1',
  type: 'image',
  name: 'failure.png',
  dataUrl: 'data:image/png;base64,abc',
  mimeType: 'image/png',
  size: 3,
};

describe('composer drafts', () => {
  it('keeps text and attachments isolated by flow session', () => {
    let drafts = updateComposerDrafts({}, 'flow-a', { text: '修复流程 A', attachments: [image] });
    drafts = updateComposerDrafts(drafts, 'flow-b', { text: '检查流程 B', attachments: [] });

    expect(getComposerDraft(drafts, 'flow-a')).toEqual({ text: '修复流程 A', attachments: [image] });
    expect(getComposerDraft(drafts, 'flow-b')).toEqual({ text: '检查流程 B', attachments: [] });
  });

  it('removes an empty draft without changing other sessions', () => {
    const drafts = {
      'flow-a': { text: 'A', attachments: [] },
      'flow-b': { text: 'B', attachments: [] },
    };

    const next = updateComposerDrafts(drafts, 'flow-a', { text: '', attachments: [] });

    expect(next).toEqual({ 'flow-b': { text: 'B', attachments: [] } });
  });
});
