import { ArrowUp, CircleAlert, Paperclip, Plus, Square, X } from 'lucide-react';
import type { ClipboardEvent, DragEvent, KeyboardEvent, ReactElement } from 'react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { AiAttachment } from './aiPanelTypes';
import { cn } from '../../../lib/utils';
import { useAiModelCatalogStore } from '../../../stores/useAiModelCatalogStore';
import { ModelSelector } from './ModelSelector';
import type { AiComposerDraft, AiComposerDraftSetter } from './composerDraft';
import { attachmentType } from './attachmentInput';

function nanoid(): string {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

type AttachmentResult = { attachment: AiAttachment } | { error: string };

async function fileToAttachment(file: File): Promise<AttachmentResult> {
  try {
    const type = attachmentType(file.name, file.type);
    if (!type) return { error: `${file.name}：不支持此文件类型` };

    const dataUrl = await readFileAsDataUrl(file);
    return {
      attachment: {
        id: nanoid(),
        type,
        name: file.name,
        dataUrl,
        mimeType: file.type || 'application/octet-stream',
        size: file.size,
      },
    };
  } catch {
    return { error: `${file.name}：读取失败，请重新选择` };
  }
}

const MIN_HEIGHT = 44;
const MAX_HEIGHT = 220;

export function ChatInput({
  pending,
  history,
  autoFocus,
  draft,
  sessionKey,
  setDraft,
  model,
  onModelChange,
  onSend,
  onStop,
}: {
  pending: boolean;
  history: string[];
  autoFocus?: boolean;
  draft: AiComposerDraft;
  sessionKey: string;
  setDraft: AiComposerDraftSetter;
  model: string;
  onModelChange: (model: string) => void;
  onSend: (text: string, attachments?: AiAttachment[]) => void;
  onStop: () => void;
}): ReactElement {
  const { attachments, text } = draft;
  const [historyCursor, setHistoryCursor] = useState({ sessionKey, index: -1 });
  const [attachmentErrorState, setAttachmentErrorState] = useState({ sessionKey, errors: [] as string[] });
  const [dragOver, setDragOver] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const histIdx = historyCursor.sessionKey === sessionKey ? historyCursor.index : -1;
  const attachmentErrors = attachmentErrorState.sessionKey === sessionKey ? attachmentErrorState.errors : [];
  const setHistIdx = useCallback((index: number): void => setHistoryCursor({ sessionKey, index }), [sessionKey]);
  const setAttachmentErrors = useCallback(
    (errors: string[]): void => setAttachmentErrorState({ sessionKey, errors }),
    [sessionKey],
  );

  useEffect(() => {
    if (autoFocus) textareaRef.current?.focus();
  }, [autoFocus]);

  // 手动清空输入即视为退出历史浏览，下次按 ↑ 重新从最近一条开始
  const handleTextChange = (value: string): void => {
    setDraft((current) => ({ ...current, text: value }));
    if (value === '') setHistIdx(-1);
  };

  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, el.scrollHeight))}px`;
  }, [text]);

  const modelIsTextOnly = useAiModelCatalogStore(
    (s) => s.models.find((m) => m.id === model)?.no_vision ?? false
  );
  const loadCatalog = useAiModelCatalogStore((s) => s.load);
  useEffect(() => { void loadCatalog(); }, [loadCatalog]);
  const visionWarning = attachments.some((attachment) => attachment.type === 'image') && modelIsTextOnly;
  const canSend = (text.trim().length > 0 || attachments.length > 0) && !pending && !visionWarning;

  const handleSend = (): void => {
    if (!canSend) return;
    onSend(text.trim(), attachments.length > 0 ? attachments : undefined);
    setDraft({ text: '', attachments: [] });
    setAttachmentErrors([]);
    setHistIdx(-1);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): void => {
    const el = textareaRef.current;

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
      return;
    }

    if (e.key === 'ArrowUp' && el?.selectionStart === 0 && el?.selectionEnd === 0) {
      if (history.length === 0) return;
      e.preventDefault();
      const nextIdx = histIdx === -1 ? history.length - 1 : Math.max(0, histIdx - 1);
      setHistIdx(nextIdx);
      const entry = history[nextIdx] ?? '';
      setDraft((current) => ({ ...current, text: entry }));
      // setSelectionRange 需等 setText 触发的重渲染把新值刷进 DOM 后才生效，故延到下一帧
      requestAnimationFrame(() => {
        if (textareaRef.current) textareaRef.current.setSelectionRange(0, 0);
      });
      return;
    }

    if (e.key === 'ArrowDown' && el && el.selectionStart === el.value.length) {
      if (histIdx === -1) return;
      e.preventDefault();
      const nextIdx = histIdx >= history.length - 1 ? -1 : histIdx + 1;
      setHistIdx(nextIdx);
      const entry = nextIdx === -1 ? '' : (history[nextIdx] ?? '');
      setDraft((current) => ({ ...current, text: entry }));
      requestAnimationFrame(() => {
        if (textareaRef.current) {
          const len = textareaRef.current.value.length;
          textareaRef.current.setSelectionRange(len, len);
        }
      });
    }
  };

  const handlePaste = useCallback(async (e: ClipboardEvent<HTMLTextAreaElement>): Promise<void> => {
    const items = Array.from(e.clipboardData.items);
    const imageItems = items.filter(i => i.type.startsWith('image/'));
    if (imageItems.length === 0) return;
    e.preventDefault();
    const newAttachments: AiAttachment[] = [];
    const errors: string[] = [];
    for (const item of imageItems) {
      const file = item.getAsFile();
      if (!file) continue;
      const result = await fileToAttachment(file);
      if ('attachment' in result) newAttachments.push(result.attachment);
      else errors.push(result.error);
    }
    if (newAttachments.length > 0) {
      setDraft((current) => ({ ...current, attachments: [...current.attachments, ...newAttachments] }));
    }
    setAttachmentErrors(errors);
  }, [setAttachmentErrors, setDraft]);

  const handleFileChange = useCallback(async (): Promise<void> => {
    const input = fileInputRef.current;
    if (!input?.files?.length) return;
    const newAttachments: AiAttachment[] = [];
    const errors: string[] = [];
    for (const file of Array.from(input.files)) {
      const result = await fileToAttachment(file);
      if ('attachment' in result) newAttachments.push(result.attachment);
      else errors.push(result.error);
    }
    input.value = '';
    if (newAttachments.length > 0) {
      setDraft((current) => ({ ...current, attachments: [...current.attachments, ...newAttachments] }));
    }
    setAttachmentErrors(errors);
  }, [setAttachmentErrors, setDraft]);

  const handleDrop = useCallback(async (e: DragEvent<HTMLDivElement>): Promise<void> => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    const newAttachments: AiAttachment[] = [];
    const errors: string[] = [];
    for (const file of files) {
      const result = await fileToAttachment(file);
      if ('attachment' in result) newAttachments.push(result.attachment);
      else errors.push(result.error);
    }
    if (newAttachments.length > 0) {
      setDraft((current) => ({ ...current, attachments: [...current.attachments, ...newAttachments] }));
    }
    setAttachmentErrors(errors);
  }, [setAttachmentErrors, setDraft]);

  return (
    <div className="shrink-0 bg-white px-3 pb-2.5 pt-1.5">
      {visionWarning && (
        <div className="mb-1.5 flex items-start gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-800" role="alert">
          <CircleAlert aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
          <span>当前模型不支持图片。请在输入框下方切换到支持视觉的模型后再发送。</span>
        </div>
      )}

      {attachmentErrors.length > 0 && (
        <div className="mb-1.5 flex items-start gap-1.5 rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5 text-[11px] text-red-700" role="alert">
          <CircleAlert aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
          <div className="min-w-0 flex-1 space-y-0.5">
            {attachmentErrors.map((error, index) => <p className="wrap-break-word" key={`${error}-${index}`}>{error}</p>)}
          </div>
          <button
            aria-label="关闭附件错误"
            className="grid h-5 w-5 shrink-0 place-items-center rounded text-red-600 hover:bg-red-100"
            onClick={() => setAttachmentErrors([])}
            type="button"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      )}

      <div
        className={cn(
          'rounded-2xl border bg-slate-50/80 transition-[border-color,box-shadow] duration-150',
          // 焦点态由卡片承担，所以 border 必须是实心 accent(4.47:1)——textarea 自己那圈方框
          // 落在圆角卡片内侧，看着像框错了东西，已由 .focus-by-container 压掉
          'focus-within:border-accent focus-within:bg-white focus-within:shadow-[0_1px_2px_rgba(15,23,42,0.04),0_0_0_3px_rgba(55,51,230,0.10)]',
          dragOver ? 'border-accent-line border-dashed bg-accent-soft' : 'border-slate-200',
          pending && !dragOver && 'border-accent-line'
        )}
        onDragEnter={() => setDragOver(true)}
        onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragOver(false); }}
        onDragOver={e => e.preventDefault()}
        onDrop={e => void handleDrop(e)}
      >
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-3 pt-2.5">
            {attachments.map(att => (
              <div
                className="group flex max-w-40 items-center gap-1.5 rounded-lg border border-slate-200 bg-white py-1 pl-1 pr-1.5"
                key={att.id}
              >
                {att.type === 'image' ? (
                  <img alt={att.name} className="h-6 w-6 rounded object-cover" src={att.dataUrl} />
                ) : (
                  <span className="flex h-6 w-6 items-center justify-center rounded bg-slate-100">
                    <Paperclip className="h-3 w-3 text-slate-400" />
                  </span>
                )}
                <span className="min-w-0 truncate text-[11px] text-slate-600">{att.name}</span>
                {/* 24px 是指针环境下的目标下限；靠 h-6 与缩略图同高，不会把 chip 撑开。
                    名字要进可访问名——一次会话可以挂多个附件，光念「按钮」分不出删的是哪个。 */}
                <button
                  aria-label={`移除附件 ${att.name}`}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-slate-500 transition-colors hover:bg-slate-100 hover:text-red-600"
                  onClick={() => setDraft((current) => ({
                    ...current,
                    attachments: current.attachments.filter((attachment) => attachment.id !== att.id),
                  }))}
                  type="button"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        <textarea
          aria-label="给 RPA 助手发送消息"
          ref={textareaRef}
          className="focus-by-container block w-full resize-none bg-transparent px-3.5 pt-3 text-[12.5px] leading-relaxed text-slate-700 placeholder:text-slate-500"
          onChange={e => handleTextChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={e => void handlePaste(e)}
          placeholder={pending ? '正在生成回复…' : '输入你想做的操作，按回车发送'}
          rows={1}
          style={{ minHeight: MIN_HEIGHT, maxHeight: MAX_HEIGHT }}
          value={text}
          disabled={pending}
        />

        <div className="flex items-center gap-1 px-2 pb-2 pt-1">
          <button
            aria-label="附加文件或图片"
            className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-600 disabled:opacity-40"
            disabled={pending}
            onClick={() => fileInputRef.current?.click()}
            title="附加文件或图片"
            type="button"
          >
            <Plus className="h-4 w-4" strokeWidth={2} />
          </button>

          <ModelSelector disabled={pending} onChange={onModelChange} placement="up" value={model} variant="ghost" />

          <div className="flex-1" />

          {pending ? (
            <button
              className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-800 text-white transition-colors hover:bg-slate-900"
              onClick={onStop}
              title="停止生成"
              type="button"
            >
              <Square className="h-3 w-3" fill="currentColor" strokeWidth={0} />
            </button>
          ) : (
            <button
              className={cn(
                'flex h-7 w-7 items-center justify-center rounded-full transition-[background-color,color,transform]',
                canSend
                  ? 'bg-accent text-white hover:bg-accent-strong active:scale-95'
                  : 'cursor-not-allowed bg-slate-200 text-slate-400'
              )}
              disabled={!canSend}
              onClick={handleSend}
              title={visionWarning ? '当前模型不支持图片' : '发送 (Enter)'}
              type="button"
            >
              <ArrowUp className="h-4 w-4" strokeWidth={2.5} />
            </button>
          )}
        </div>
      </div>

      <p className="mt-1.5 px-1 text-center text-[10px] text-slate-500">
        Shift+Enter 换行 · ↑↓ 翻历史 · 支持粘贴截图
      </p>

      <input
        accept="image/*,.txt,.md,.json,.yaml,.yml,.csv,.xml,.js,.ts,.py,.sh"
        className="hidden"
        multiple
        onChange={() => void handleFileChange()}
        ref={fileInputRef}
        type="file"
      />
    </div>
  );
}
