/* ── CodeBlock / InlineCode ────────────────────────────────────────────────────
   The single, encapsulated home for rendering code in the app. Every surface that
   shows code — generated Scrapling scripts, AI-chat fenced blocks, markdown report
   code, tool-call JSON — routes through here so highlighting, fonts, chrome, and
   copy behaviour stay identical. Built on react-syntax-highlighter's PrismLight
   with a curated language set (smaller than the full Prism build). */
import { Check, Copy } from 'lucide-react';
import { useCallback, useRef, useState, type ReactElement } from 'react';
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter';
import bash from 'react-syntax-highlighter/dist/esm/languages/prism/bash';
import css from 'react-syntax-highlighter/dist/esm/languages/prism/css';
import http from 'react-syntax-highlighter/dist/esm/languages/prism/http';
import javascript from 'react-syntax-highlighter/dist/esm/languages/prism/javascript';
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json';
import jsx from 'react-syntax-highlighter/dist/esm/languages/prism/jsx';
import markdown from 'react-syntax-highlighter/dist/esm/languages/prism/markdown';
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python';
import sql from 'react-syntax-highlighter/dist/esm/languages/prism/sql';
import tsx from 'react-syntax-highlighter/dist/esm/languages/prism/tsx';
import typescript from 'react-syntax-highlighter/dist/esm/languages/prism/typescript';
import yaml from 'react-syntax-highlighter/dist/esm/languages/prism/yaml';
import { oneDark, oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism';

import { cn } from '../../lib/utils';

// Register the curated set once at module load.
for (const [name, lang] of [
  ['bash', bash], ['css', css], ['http', http], ['javascript', javascript],
  ['json', json], ['jsx', jsx], ['markdown', markdown], ['python', python],
  ['sql', sql], ['tsx', tsx], ['typescript', typescript], ['yaml', yaml],
] as const) {
  SyntaxHighlighter.registerLanguage(name, lang);
}

// Common aliases → registered canonical names.
const LANG_ALIAS: Record<string, string> = {
  py: 'python', js: 'javascript', ts: 'typescript', sh: 'bash', shell: 'bash',
  zsh: 'bash', yml: 'yaml', md: 'markdown', htm: 'markup', html: 'markup',
};

function normalizeLang(language?: string): string {
  if (language === undefined || language === '') return 'text';
  const key = language.toLowerCase();
  return LANG_ALIAS[key] ?? key;
}

const MONO = 'var(--font-mono)';

export type CodeBlockVariant = 'light' | 'dark';

export interface CodeBlockProps {
  code: string;
  language?: string;
  /** Visual surface. `light` for content/chat, `dark` for the console look. */
  variant?: CodeBlockVariant;
  /** Optional header label (e.g. a filename); shows the header bar when set. */
  filename?: string;
  /** Optional right-aligned note in the header (e.g. a dependency / language). */
  note?: string;
  showLineNumbers?: boolean;
  wrap?: boolean;
  maxHeight?: number | string;
  showCopy?: boolean;
  className?: string;
}

function CopyButton({ value, variant }: { value: string; variant: CodeBlockVariant }): ReactElement {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(() => {
    void navigator.clipboard.writeText(value).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    });
  }, [value]);
  return (
    <button
      aria-label={copied ? '已复制' : '复制代码'}
      className={cn(
        'inline-flex h-6 w-6 items-center justify-center rounded-md border transition-colors',
        variant === 'dark'
          ? 'border-white/10 bg-white/5 text-slate-300 hover:bg-white/10 hover:text-white'
          : 'border-slate-200 bg-white/80 text-slate-400 hover:bg-slate-50 hover:text-slate-700',
      )}
      onClick={onCopy}
      type="button"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" strokeWidth={2} /> : <Copy className="h-3.5 w-3.5" strokeWidth={1.5} />}
    </button>
  );
}

export function CodeBlock({
  code,
  language,
  variant = 'light',
  filename,
  note,
  showLineNumbers = false,
  wrap = false,
  maxHeight,
  showCopy = true,
  className,
}: CodeBlockProps): ReactElement {
  const lang = normalizeLang(language);
  const dark = variant === 'dark';
  const hasHeader = filename !== undefined || note !== undefined;

  return (
    <div
      className={cn(
        'group relative overflow-hidden rounded-md border text-[11px]',
        dark ? 'border-slate-800 bg-[#0f172a]' : 'border-slate-200 bg-slate-50',
        className,
      )}
    >
      {hasHeader && (
        <div
          className={cn(
            'flex items-center justify-between gap-2 border-b px-2.5 py-1.5 font-mono text-[10px]',
            dark ? 'border-white/10 text-slate-400' : 'border-slate-200 text-slate-500',
          )}
        >
          <span className="min-w-0 truncate">{filename}</span>
          {note !== undefined && <span className="shrink-0 opacity-80">{note}</span>}
        </div>
      )}

      {showCopy && (
        <div className={cn('absolute right-1.5 z-10 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100', hasHeader ? 'top-9' : 'top-1.5')}>
          <CopyButton value={code} variant={variant} />
        </div>
      )}

      <div style={{ maxHeight: maxHeight ?? undefined, overflow: 'auto' }}>
        <SyntaxHighlighter
          PreTag="div"
          codeTagProps={{ style: { fontFamily: MONO, fontSize: 'inherit' } }}
          customStyle={{
            margin: 0,
            background: 'transparent',
            padding: '10px 12px',
            fontSize: '11px',
            lineHeight: 1.6,
            fontFamily: MONO,
          }}
          language={lang}
          showLineNumbers={showLineNumbers}
          style={dark ? oneDark : oneLight}
          wrapLongLines={wrap}
        >
          {code}
        </SyntaxHighlighter>
      </div>
    </div>
  );
}

/** Inline code inside running text. */
export function InlineCode({ children, className }: { children: React.ReactNode; className?: string }): ReactElement {
  const text = typeof children === 'string' ? children : Array.isArray(children) ? children.join('') : '';
  if (/^https?:\/\/\S+$/.test(text)) {
    return (
      <a
        className={cn('rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.92em] text-accent-strong underline decoration-accent-line underline-offset-2 hover:text-accent-press', className)}
        href={text}
        rel="noopener noreferrer"
        target="_blank"
      >
        {children}
      </a>
    );
  }
  return (
    <code className={cn('rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.92em] text-slate-800', className)}>
      {children}
    </code>
  );
}

// Typography shared by the editor's highlight layer and its textarea so the two
// overlap pixel-for-pixel. Wrapping is on (narrow panels read better wrapped).
const EDITOR_TYPE = {
  fontFamily: MONO,
  fontSize: '12px',
  lineHeight: 1.6,
  tabSize: 2,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  overflowWrap: 'break-word',
} as const;
const EDITOR_PAD = '10px 12px';

export interface CodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  language?: string;
  variant?: CodeBlockVariant;
  label?: string;
  placeholder?: string;
  /** Fixed editor height in px. When set, the editor is scrollable instead of growing. */
  height?: number;
  /** Minimum editor height in px (ignored when `height` is set). */
  minHeight?: number;
  className?: string;
}

/**
 * Editable code field with live syntax highlighting — a transparent textarea
 * layered over the highlighter (same technique as react-simple-code-editor).
 * Use this where code must be edited; use `CodeBlock` for read-only display.
 */
export function CodeEditor({
  value,
  onChange,
  language,
  variant = 'light',
  label,
  placeholder,
  height,
  minHeight = 160,
  className,
}: CodeEditorProps): ReactElement {
  const lang = normalizeLang(language);
  const dark = variant === 'dark';
  const taRef = useRef<HTMLTextAreaElement>(null);
  const hlRef = useRef<HTMLDivElement>(null);
  const fixed = height !== undefined;

  const handleScroll = useCallback((e: React.UIEvent<HTMLTextAreaElement>) => {
    if (hlRef.current) hlRef.current.scrollTop = e.currentTarget.scrollTop;
  }, []);

  return (
    <div
      className={cn(
        'group overflow-hidden rounded-md border transition focus-within:ring-2',
        dark
          ? 'border-slate-800 bg-[#0f172a] focus-within:border-[var(--color-accent)] focus-within:ring-accent-soft'
          : 'border-slate-200 bg-slate-50 focus-within:border-[var(--color-accent)] focus-within:ring-accent-soft',
        fixed && 'flex flex-col',
        className,
      )}
      onClick={() => taRef.current?.focus()}
      role="presentation"
      style={fixed ? { height } : undefined}
    >
      {/* Header bar — mirrors CodeBlock's chrome so the two components look identical. */}
      {label !== undefined && (
        <div
          className={cn(
            'flex shrink-0 items-center justify-between gap-2 border-b px-2.5 py-1.5 font-mono text-[10px]',
            dark ? 'border-white/10 text-slate-400' : 'border-slate-200 text-slate-500',
          )}
        >
          <span className="min-w-0 truncate">{label}</span>
          <CopyButton value={value} variant={variant} />
        </div>
      )}

      {/* Code area — grows with content (no height) or fills flex remainder (fixed height). */}
      <div className={cn('relative', fixed && 'min-h-0 flex-1 overflow-hidden')}>
        {/* Highlight layer — in-flow when growing; scrollTop synced from textarea when fixed. */}
        <div aria-hidden className="pointer-events-none" ref={hlRef} style={fixed ? { height: '100%', overflow: 'hidden' } : undefined}>
          <SyntaxHighlighter
            PreTag="div"
            codeTagProps={{ style: { ...EDITOR_TYPE } }}
            customStyle={{
              margin: 0,
              background: 'transparent',
              padding: EDITOR_PAD,
              minHeight: fixed ? '100%' : minHeight,
              ...EDITOR_TYPE,
            }}
            language={lang}
            style={dark ? oneDark : oneLight}
            wrapLongLines
          >
            {value === '' ? ' ' : `${value}\n`}
          </SyntaxHighlighter>
        </div>
        {/* Editable layer — transparent text, visible caret, sits exactly on top. */}
        <textarea
          className={cn(
            'absolute inset-0 h-full w-full resize-none border-0 bg-transparent outline-none',
            fixed ? 'overflow-y-auto' : 'overflow-hidden',
          )}
          onChange={(event) => onChange(event.target.value)}
          onScroll={fixed ? handleScroll : undefined}
          placeholder={placeholder}
          ref={taRef}
          spellCheck={false}
          style={{
            padding: EDITOR_PAD,
            ...EDITOR_TYPE,
            color: 'transparent',
            caretColor: dark ? '#e2e8f0' : '#0f172a',
          }}
          value={value}
        />
      </div>
    </div>
  );
}
