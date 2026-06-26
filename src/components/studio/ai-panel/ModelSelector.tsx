import { ChevronDown, WifiOff } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useRef, useState } from 'react';
import { DEFAULT_BROWSER_BACKEND_URL } from '../../../lib/backendClient';
import { cn } from '../../../lib/utils';

const API = DEFAULT_BROWSER_BACKEND_URL;

interface ModelEntry {
  id: string;
  label: string;
  provider: string;
  configured?: boolean;
  local?: boolean;
  recommended?: boolean;
}

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  google: 'Google',
  deepseek: 'DeepSeek',
  qwen: '阿里云 Qwen',
  zai: '智谱 GLM',
};

const MODEL_BADGES: Record<string, string> = {
  'claude-fable-5': '最新',
  'claude-sonnet-4-6': '推荐',
  'claude-opus-4-8': '最强',
  'claude-haiku-4-5': '快速',
  'gpt-5.5': '最新',
  'gpt-5.4-mini': '快速',
  'gpt-4.1-mini': '经济',
  'o3': '推理',
  'o4-mini': '推理',
  'gemini/gemini-3.5-flash': '超快',
  'gemini/gemini-2.5-pro': '长上下文',
  'deepseek/deepseek-v4-flash': '超快',
  'deepseek/deepseek-reasoner': '推理链',
  'openai/qwen3.6-flash': '超快',
  'zai/glm-4.6': '推荐',
  'zai/glm-4.5-air': '轻量',
  'zai/glm-4.5-flash': '免费',
};

// Fallback list shown when backend is unreachable — all models are selectable
const FALLBACK_MODELS: ModelEntry[] = [
  { id: 'claude-fable-5', label: 'Claude Fable 5', provider: 'anthropic', configured: true },
  { id: 'claude-opus-4-8', label: 'Claude Opus 4.8', provider: 'anthropic', configured: true },
  { id: 'claude-sonnet-4-6', label: 'Claude Sonnet 4.6', provider: 'anthropic', configured: true },
  { id: 'claude-haiku-4-5', label: 'Claude Haiku 4.5', provider: 'anthropic', configured: true },
  { id: 'gpt-5.5', label: 'GPT-5.5', provider: 'openai', configured: true },
  { id: 'gpt-5.4', label: 'GPT-5.4', provider: 'openai', configured: true },
  { id: 'gpt-5.4-mini', label: 'GPT-5.4 mini', provider: 'openai', configured: true },
  { id: 'gpt-4.1', label: 'GPT-4.1', provider: 'openai', configured: true },
  { id: 'gpt-4.1-mini', label: 'GPT-4.1 mini', provider: 'openai', configured: true },
  { id: 'o3', label: 'o3', provider: 'openai', configured: true },
  { id: 'o4-mini', label: 'o4-mini', provider: 'openai', configured: true },
  { id: 'gemini/gemini-3.5-flash', label: 'Gemini 3.5 Flash', provider: 'google', configured: true },
  { id: 'gemini/gemini-2.5-pro', label: 'Gemini 2.5 Pro', provider: 'google', configured: true },
  { id: 'gemini/gemini-2.5-flash', label: 'Gemini 2.5 Flash', provider: 'google', configured: true },
  { id: 'deepseek/deepseek-v4-flash', label: 'DeepSeek V4 Flash', provider: 'deepseek', configured: true },
  { id: 'deepseek/deepseek-v4-pro', label: 'DeepSeek V4 Pro', provider: 'deepseek', configured: true },
  { id: 'deepseek/deepseek-chat', label: 'DeepSeek Chat', provider: 'deepseek', configured: true },
  { id: 'deepseek/deepseek-reasoner', label: 'DeepSeek R1', provider: 'deepseek', configured: true },
  { id: 'openai/qwen3.7-max', label: 'Qwen3.7 Max', provider: 'qwen', configured: true },
  { id: 'openai/qwen3.6-flash', label: 'Qwen3.6 Flash', provider: 'qwen', configured: true },
  { id: 'openai/qwen3-235b-a22b', label: 'Qwen3 235B', provider: 'qwen', configured: true },
  { id: 'openai/qwen3-32b', label: 'Qwen3 32B', provider: 'qwen', configured: true },
  { id: 'zai/glm-4.6', label: 'GLM-4.6', provider: 'zai', configured: true },
  { id: 'zai/glm-4.5', label: 'GLM-4.5', provider: 'zai', configured: true },
  { id: 'zai/glm-4.5-air', label: 'GLM-4.5 Air', provider: 'zai', configured: true },
  { id: 'zai/glm-4.5-flash', label: 'GLM-4.5 Flash', provider: 'zai', configured: true },
];

export function ModelSelector({
  value,
  onChange,
  placement = 'down',
  variant = 'bordered',
  disabled = false,
}: {
  value: string;
  onChange: (model: string) => void;
  /** Whether the menu opens below ('down') or above ('up') the trigger. */
  placement?: 'down' | 'up';
  /** 'bordered' for the panel header, 'ghost' for the composer toolbar. */
  variant?: 'bordered' | 'ghost';
  /** When true the trigger is non-interactive (e.g. while AI is streaming). */
  disabled?: boolean;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [backendDown, setBackendDown] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setBackendDown(false);

    const controller = new AbortController();
    fetch(`${API}/api/ai/models`, { signal: controller.signal })
      .then((r) => r.json())
      .then((data: { models: ModelEntry[] }) => {
        if (Array.isArray(data.models) && data.models.length > 0) {
          setModels(data.models);
          setBackendDown(false);
        } else {
          setBackendDown(true);
        }
      })
      .catch((err) => {
        // Ignore abort errors — dropdown closed before fetch completed
        if ((err as Error).name !== 'AbortError') setBackendDown(true);
      });

    return () => controller.abort();
  }, [open]);

  useEffect(() => {
    const handler = (e: MouseEvent): void => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Use live models when available, fallback list when backend is down
  const displayModels = backendDown ? FALLBACK_MODELS : models;

  // Find current model label — also search fallback list so the button always shows a name
  const allModels = models.length > 0 ? models : FALLBACK_MODELS;
  const current = allModels.find((m) => m.id === value);
  const currentLabel = current?.label ?? value;

  const grouped = displayModels.reduce<Record<string, ModelEntry[]>>((acc, m) => {
    const group = m.provider ?? 'other';
    (acc[group] ??= []).push(m);
    return acc;
  }, {});

  return (
    <div className="relative" ref={ref}>
      <button
        className={cn(
          'flex h-6 items-center gap-1 rounded-md text-[11px] transition-colors',
          variant === 'ghost'
            ? 'px-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-700'
            : 'border border-slate-200 bg-white px-2 text-slate-600 hover:border-slate-300 hover:bg-slate-50',
          open && variant === 'ghost' && 'bg-slate-100 text-slate-700',
          disabled && 'cursor-not-allowed opacity-50',
        )}
        disabled={disabled}
        onClick={() => !disabled && setOpen((o) => !o)}
        title={disabled ? '生成中，下次发送时生效' : undefined}
        type="button"
      >
        <span className="max-w-30 truncate font-medium">{currentLabel}</span>
        <ChevronDown className={cn('h-3 w-3 shrink-0 text-slate-400 transition-transform', open && 'rotate-180')} />
      </button>

      {open && !disabled && (
        <div className={cn(
          'absolute left-0 z-50 w-56 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg',
          placement === 'up' ? 'bottom-7' : 'top-7'
        )}>
          {backendDown && (
            <div className="flex items-center gap-1.5 border-b border-amber-100 bg-amber-50 px-2.5 py-1.5 text-[10px] text-amber-600">
              <WifiOff className="h-3 w-3 shrink-0" />
              后端离线，显示全部模型
            </div>
          )}
          <div className="max-h-72 overflow-y-auto">
            {Object.entries(grouped).map(([provider, items]) => (
              <div key={provider}>
                <div className="sticky top-0 bg-slate-50 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                  {PROVIDER_LABELS[provider] ?? provider}
                </div>
                {items.map((m) => {
                  const unavailable = !backendDown && m.configured === false && !m.local;
                  const badge = MODEL_BADGES[m.id];
                  return (
                    <button
                      className={cn(
                        'flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[11px] hover:bg-slate-50',
                        m.id === value && 'bg-accent-soft text-accent-strong',
                        unavailable && 'opacity-40 cursor-not-allowed'
                      )}
                      disabled={unavailable}
                      key={m.id}
                      onClick={() => { onChange(m.id); setOpen(false); }}
                      type="button"
                    >
                      <span className="flex-1 truncate">{m.label}</span>
                      {badge && (
                        <span className="shrink-0 rounded bg-accent-soft px-1 py-0.5 text-[9px] font-medium text-accent-strong">
                          {badge}
                        </span>
                      )}
                      {unavailable && (
                        <span className="shrink-0 text-[9px] text-slate-400">未配置</span>
                      )}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
          <div className="border-t border-slate-100 px-2.5 py-1.5 text-[10px] text-slate-400">
            {backendDown ? '启动后端后配置状态将自动更新' : '在设置页配置 API Key'}
          </div>
        </div>
      )}
    </div>
  );
}
