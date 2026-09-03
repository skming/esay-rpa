import { ArrowUp, Paperclip, Plus, Square, X } from 'lucide-react';
import type { ClipboardEvent, DragEvent, KeyboardEvent, ReactElement } from 'react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { AiAttachment } from './aiPanelTypes';
import { cn } from '../../../lib/utils';
import { useAiModelCatalogStore } from '../../../stores/useAiModelCatalogStore';
import { ModelSelector } from './ModelSelector';

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

async function fileToAttachment(file: File): Promise<AiAttachment | null> {
  try {
    const isImage = file.type.startsWith('image/');
    const isSupportedText = /^(text\/|application\/(json|xml|javascript|typescript|x-yaml))/.test(file.type) || /\.(txt|md|json|yaml|yml|csv|xml|js|ts|py|sh)$/i.test(file.name);

    if (!isImage && !isSupportedText) return null;

    const dataUrl = await readFileAsDataUrl(file);
    return {
      id: nanoid(),
      type: isImage ? 'image' : 'file',
      name: file.name,
      dataUrl,
      mimeType: file.type || 'application/octet-stream',
      size: file.size,
    };
  } catch {
    return null;
  }
}

const MIN_HEIGHT = 44;
const MAX_HEIGHT = 220;

export function ChatInput({
  pending,
  thinking,
  history,
  autoFocus,
  model,
  onModelChange,
  onSend,
  onStop,
}: {
  pending: boolean;
  thinking?: boolean;
  history: string[];
  autoFocus?: boolean;
  model: string;
  onModelChange: (model: string) => void;
  onSend: (text: string, attachments?: AiAttachment[]) => void;
  onStop: () => void;
}): ReactElement {
  const [text, setText] = useState('');
  const [histIdx, setHistIdx] = useState(-1);
  const [attachments, setAttachments] = useState<AiAttachment[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (autoFocus) textareaRef.current?.focus();
  }, [autoFocus]);

  // 手动清空输入即视为退出历史浏览，下次按 ↑ 重新从最近一条开始
  const handleTextChange = (value: string): void => {
    setText(value);
    if (value === '') setHistIdx(-1);
  };

  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, el.scrollHeight))}px`;
  }, [text]);

  const handleSend = (): void => {
    const trimmed = text.trim();
    if ((!trimmed && attachments.length === 0) || pending) return;
    onSend(trimmed, attachments.length > 0 ? attachments : undefined);
    setText('');
    setHistIdx(-1);
    setAttachments([]);
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
      setText(entry);
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
      setText(entry);
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
    for (const item of imageItems) {
      const file = item.getAsFile();
      if (!file) continue;
      const att = await fileToAttachment(file);
      if (att) newAttachments.push(att);
    }
    if (newAttachments.length > 0) {
      setAttachments(prev => [...prev, ...newAttachments]);
    }
  }, []);

  const handleFileChange = useCallback(async (): Promise<void> => {
    const input = fileInputRef.current;
    if (!input?.files?.length) return;
    const newAttachments: AiAttachment[] = [];
    for (const file of Array.from(input.files)) {
      const att = await fileToAttachment(file);
      if (att) newAttachments.push(att);
    }
    input.value = '';
    if (newAttachments.length > 0) setAttachments(prev => [...prev, ...newAttachments]);
  }, []);

  const handleDrop = useCallback(async (e: DragEvent<HTMLDivElement>): Promise<void> => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    const newAttachments: AiAttachment[] = [];
    for (const file of files) {
      const att = await fileToAttachment(file);
      if (att) newAttachments.push(att);
    }
    if (newAttachments.length > 0) setAttachments(prev => [...prev, ...newAttachments]);
  }, []);

  const canSend = (text.trim().length > 0 || attachments.length > 0) && !pending;
  // 附图后发给纯文本模型会直接报错，提前警示用户切换；能力以后端目录为准，
  // 前端再维护一份名单只会在新增模型时悄悄过期
  // 订阅派生出的布尔量而不是取 store 方法：方法引用恒定，目录拉回来时不会触发重渲染
  const modelIsTextOnly = useAiModelCatalogStore(
    (s) => s.models.find((m) => m.id === model)?.no_vision ?? false
  );
  const loadCatalog = useAiModelCatalogStore((s) => s.load);
  useEffect(() => { void loadCatalog(); }, [loadCatalog]);
  const visionWarning = attachments.some(a => a.type === 'image') && modelIsTextOnly;

  return (
    <div className="shrink-0 bg-white px-3 pb-2.5 pt-1.5">
      {visionWarning && (
        <div className="mb-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-700">
          当前模型不支持图片输入，发送后会报错。请切换到 Claude、GPT 或 Gemini 等支持视觉的模型。
        </div>
      )}

      <div
        className={cn(
          'rounded-2xl border bg-slate-50/80 transition-[border-color,box-shadow] duration-150',
          // 焦点态由卡片承担，所以 border 必须是实心 accent(4.47:1)——textarea 自己那圈方框
          // 落在圆角卡片内侧，看着像框错了东西，已由 .focus-by-container 压掉
          'focus-within:border-accent focus-within:bg-white focus-within:shadow-[0_1px_2px_rgba(15,23,42,0.04),0_0_0_3px_rgba(55,51,230,0.10)]',
          dragOver ? 'border-accent-line border-dashed bg-accent-soft' : 'border-slate-200',
          pending && !dragOver && 'border-accent-line animate-breath-glow'
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
                  onClick={() => setAttachments(prev => prev.filter(a => a.id !== att.id))}
                  type="button"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        <textarea
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
            className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-600 disabled:opacity-40"
            disabled={pending}
            onClick={() => fileInputRef.current?.click()}
            title="附加文件或图片"
            type="button"
          >
            <Plus className="h-4 w-4" strokeWidth={2} />
          </button>

          <ModelSelector disabled={pending} onChange={onModelChange} placement="up" value={model} variant="ghost" />

          {pending && thinking && (
            <div className="flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">
              <span className="animate-thinking-dot inline-block h-1 w-1 rounded-full bg-slate-400 [animation-delay:0ms]" />
              <span className="animate-thinking-dot inline-block h-1 w-1 rounded-full bg-slate-400 [animation-delay:150ms]" />
              <span className="animate-thinking-dot inline-block h-1 w-1 rounded-full bg-slate-400 [animation-delay:300ms]" />
              <span>思考中</span>
            </div>
          )}

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
              title="发送 (Enter)"
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
        accept="image/*,.txt,.md,.json,.yaml,.yml,.csv,.xml,.js,.ts,.py"
        className="hidden"
        multiple
        onChange={() => void handleFileChange()}
        ref={fileInputRef}
        type="file"
      />
    </div>
  );
}
