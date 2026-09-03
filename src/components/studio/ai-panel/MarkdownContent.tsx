import { ArrowUpRight } from 'lucide-react';
import type { ReactElement, ReactNode } from 'react';
import { useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { cn } from '../../../lib/utils';
import { CodeBlock, InlineCode } from '../../ui/CodeBlock';

/** AI 回复正文的 Markdown 渲染：统一排版组件映射 + 节点 id 可点定位。 */
export function MarkdownContent({ text, onFocusNode }: { text: string; onFocusNode?: (nodeId: string) => void }): ReactElement {
  const normalizedText = useMemo(() => compactNodeSummary(linkifyBareUrls(text)), [text]);
  return (
    <div className="markdown-body overflow-x-hidden text-[12px] leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ className, children, ...props }) {
            const hasLang = /language-(\w+)/.test(className ?? '');
            if (hasLang) {
              // pre wrapper below renders this — avoid double output
              return <code className={className} {...props}>{children}</code>;
            }
            const text = String(children);
            if (isNodeId(text)) {
              return (
                <button
                  className={cn(
                    'group/node-id inline-flex items-center gap-0.5 rounded bg-slate-100 px-1 py-0.5 align-baseline font-mono text-[0.92em] font-medium text-slate-800 transition-colors',
                    onFocusNode && 'cursor-pointer hover:bg-accent-soft hover:text-accent-strong'
                  )}
                  disabled={!onFocusNode}
                  onClick={() => onFocusNode?.(text)}
                  title={onFocusNode ? `定位到 ${text}` : text}
                  type="button"
                >
                  <span className={cn(onFocusNode && 'group-hover/node-id:underline group-hover/node-id:decoration-accent-strong group-hover/node-id:underline-offset-2')}>
                    {children}
                  </span>
                  {onFocusNode && <ArrowUpRight className="h-3 w-3 opacity-60 transition-opacity group-hover/node-id:opacity-100" strokeWidth={1.75} />}
                </button>
              );
            }
            return <InlineCode>{children}</InlineCode>;
          },
          pre({ children }: { children?: ReactNode }) {
            const codeEl = (children as ReactElement | null);
            const className: string = (codeEl?.props as { className?: string } | undefined)?.className ?? '';
            const match = /language-(\w+)/.exec(className);
            const lang = match?.[1] ?? '';
            const rawCode = String((codeEl?.props as { children?: ReactNode })?.children ?? '').replace(/\n$/, '');
            return <CodeBlock className="my-1.5" code={rawCode} language={lang} maxHeight={320} variant="light" />;
          },
          p({ children }: { children?: ReactNode }) {
            return <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>;
          },
          h1({ children }: { children?: ReactNode }) {
            return <h1 className="mb-1.5 mt-2 text-[14px] font-semibold text-slate-800">{children}</h1>;
          },
          h2({ children }: { children?: ReactNode }) {
            return <h2 className="mb-1 mt-2 text-[13px] font-semibold text-slate-800">{children}</h2>;
          },
          h3({ children }: { children?: ReactNode }) {
            return <h3 className="mb-1 mt-1.5 text-[12px] font-semibold text-slate-700">{children}</h3>;
          },
          ul({ children }: { children?: ReactNode }) {
            return <ul className="mb-2 ml-4 list-disc space-y-1">{children}</ul>;
          },
          ol({ children }: { children?: ReactNode }) {
            return <ol className="mb-2 ml-4 list-decimal space-y-1">{children}</ol>;
          },
          li({ children }: { children?: ReactNode }) {
            return <li className="pl-0.5 text-[12px] leading-relaxed">{children}</li>;
          },
          blockquote({ children }: { children?: ReactNode }) {
            return (
              <blockquote className="my-1.5 border-l-[3px] border-slate-300 pl-3 text-slate-500 italic">
                {children}
              </blockquote>
            );
          },
          hr() {
            return <hr className="my-2 border-slate-200" />;
          },
          a({ href, children }: { href?: string; children?: ReactNode }) {
            return (
              <a
                className="font-medium text-accent-strong underline decoration-accent-line underline-offset-2 hover:text-accent-press hover:decoration-accent-strong"
                href={href}
                rel="noopener noreferrer"
                target="_blank"
              >
                {children}
              </a>
            );
          },
          // table-fixed forces the table to obey CSS width; table-layout:auto can overflow
          // the parent even inside overflow-x-auto
          table({ children }: { children?: ReactNode }) {
            return (
              <div className="my-1.5 w-full rounded-sm border border-slate-200">
                <table className="w-full table-fixed border-collapse text-[11px]">{children}</table>
              </div>
            );
          },
          thead({ children }: { children?: ReactNode }) {
            return <thead className="bg-slate-50 [&_tr]:border-b [&_tr]:border-slate-200">{children}</thead>;
          },
          tbody({ children }: { children?: ReactNode }) {
            return <tbody className="[&_tr:last-child]:border-0">{children}</tbody>;
          },
          tr({ children }: { children?: ReactNode }) {
            return <tr className="border-b border-slate-100">{children}</tr>;
          },
          th({ children }: { children?: ReactNode }) {
            return (
              <th className="px-3 py-2 text-left align-middle font-semibold text-slate-700 wrap-break-word">
                {children}
              </th>
            );
          },
          td({ children }: { children?: ReactNode }) {
            return (
              <td className="px-3 py-2 align-middle text-slate-600 wrap-break-word">{children}</td>
            );
          },
          strong({ children }: { children?: ReactNode }) {
            return <strong className="font-semibold text-slate-800">{children}</strong>;
          },
        }}
      >
        {normalizedText}
      </ReactMarkdown>
    </div>
  );
}

// 把裸 URL 包成 markdown 链接以便渲染为可点；跳过代码围栏内的行，避免破坏代码示例里的 URL 文本
function linkifyBareUrls(text: string): string {
  let output = '';
  let inFence = false;
  for (const line of text.split('\n')) {
    if (line.trimStart().startsWith('```')) {
      inFence = !inFence;
      output += `${line}\n`;
      continue;
    }
    output += `${inFence ? line : line.replace(
      /(?<!\]\()(?<!["'`=])(https?:\/\/[^\s<>)\]}，。；、]+)/g,
      (url) => `[${url}](${url})`
    )}\n`;
  }
  return output.trimEnd();
}

// 判断内联代码片段是否是节点 id（如 n1_click），命中后渲染为可点的「定位到节点」按钮
function isNodeId(value: string): boolean {
  if (!/^[A-Za-z][\w-]*$/.test(value)) return false;
  if (!/^n\d+_/.test(value)) return false;
  return true;
}

// 把 AI 回复里「节点标题（`id`·`type`）：说明」这类啰嗦格式压缩成加粗的「标题（id）」，减少视觉噪音
function compactNodeSummary(text: string): string {
  return text
    .split('\n')
    .map((line) => {
      const nodeLine = /^(\s*[-*]\s+)(.+?)（\s*`?([A-Za-z][\w-]*)`?\s*·\s*`?[\w.-]+`?\s*）\s*[：:].*$/.exec(line);
      if (nodeLine) {
        return `${nodeLine[1]}**${nodeLine[2].trim()}（\`${nodeLine[3]}\`）**`;
      }
      return line.replace(/（\s*`?([A-Za-z][\w-]*)`?\s*·\s*`?[\w.-]+`?\s*）/g, '（`$1`）');
    })
    .join('\n');
}
