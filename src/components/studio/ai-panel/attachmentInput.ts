import type { AiAttachment } from './aiPanelTypes';

export function attachmentType(name: string, mimeType: string): AiAttachment['type'] | null {
  if (mimeType.startsWith('image/')) return 'image';
  if (/^(text\/|application\/(json|xml|javascript|typescript|x-yaml))/.test(mimeType)
    || /\.(txt|md|json|yaml|yml|csv|xml|js|ts|py|sh)$/i.test(name)) return 'file';
  return null;
}
