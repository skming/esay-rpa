import { describe, expect, it } from 'vitest';

import { attachmentType } from './attachmentInput';

describe('attachmentType', () => {
  it('accepts images and supported text files', () => {
    expect(attachmentType('page.png', 'image/png')).toBe('image');
    expect(attachmentType('flow.json', 'application/json')).toBe('file');
    expect(attachmentType('script.py', '')).toBe('file');
  });

  it('rejects unsupported binary files', () => {
    expect(attachmentType('archive.zip', 'application/zip')).toBeNull();
    expect(attachmentType('document.pdf', 'application/pdf')).toBeNull();
  });
});
