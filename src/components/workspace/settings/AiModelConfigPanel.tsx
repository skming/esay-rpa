import {
  Bot, ExternalLink, Eye, EyeOff, Loader2, Plus, Trash2, Wifi, WifiOff,
} from 'lucide-react';
import type { ReactElement } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { backend } from '../../../lib/backendClient';
import { cn } from '../../../lib/utils';
import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import type { AiConfig, AiModelMeta, AiModelsResult, AiModelTestResult, AiProviderGroupMeta } from '../../../types/electron';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '../../ui/alert-dialog';
import { Button, IconButton } from '../../ui/button';
import { Checkbox } from '../../ui/checkbox';
import { Collapsible } from '../../ui/collapsible';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../../ui/dialog';
import { SettingsContent, SettingsLoadError } from './SettingsContent';


type ProviderGroup = { key: string; label: string; env_key: string; placeholder: string; docsUrl: string };

// 这里只补充取密钥入口；厂商分组由后端 providers 提供。
const PROVIDER_HINTS: Record<string, { placeholder: string; docsUrl: string }> = {
  anthropic: { placeholder: 'sk-ant-api03-…', docsUrl: 'https://console.anthropic.com/settings/keys' },
  openai: { placeholder: 'sk-proj-…', docsUrl: 'https://platform.openai.com/api-keys' },
  google: { placeholder: 'AIza…', docsUrl: 'https://aistudio.google.com/app/apikey' },
  deepseek: { placeholder: 'sk-…', docsUrl: 'https://platform.deepseek.com/api_keys' },
  qwen: { placeholder: 'sk-…', docsUrl: 'https://dashscope.console.aliyun.com/apiKey' },
  zai: { placeholder: '…', docsUrl: 'https://z.ai/manage-apikey/apikey-list' },
  xai: { placeholder: 'xai-…', docsUrl: 'https://console.x.ai/' },
};

/** 厂商分组由后端单独提供，删除最后一个模型时仍保留该厂商的密钥和添加入口。 */
function toProviderGroups(providers: AiProviderGroupMeta[]): ProviderGroup[] {
  return providers.map(p => ({
    key: p.id,
    label: p.label,
    env_key: p.env_key,
    ...(PROVIDER_HINTS[p.id] ?? { placeholder: '…', docsUrl: '' }),
  }));
}

type TestStatus = 'idle' | 'testing' | 'ok' | 'fail';
/** 按模型 id 归属，不是按 env_key：一个服务商下各模型在中转上的可用性互不代表。 */
type TestResult = { status: TestStatus; latencyMs?: number; error?: string; servedBy?: string };
const TEST_RESULT_DURATION_MS = 6_000;

const aiFieldClass = 'h-8 w-full rounded-md border border-rule-2 bg-surface px-2.5 text-[11px] text-ink-2 outline-none transition placeholder:text-ink-3 focus-visible:border-accent-line focus-visible:ring-2 focus-visible:ring-accent-soft';
const aiMonoFieldClass = cn(aiFieldClass, 'font-mono');
// 用容器查询而非视口断点：右侧还有 220px 侧栏，按视口判断会让 4 列布局在窄可用宽度下横向溢出
const aiProviderGridClass = '@min-3xl:grid-cols-[120px_minmax(260px,1.2fr)_minmax(210px,0.85fr)_88px]';

type DraftCatalogModel = {
  context_window: string;
  id: string;
  label: string;
  recommended: boolean;
  tier: string;
};

const EMPTY_DRAFT_MODEL: DraftCatalogModel = {
  context_window: '',
  id: '',
  label: '',
  recommended: false,
  tier: 'standard',
};

type ModelDialogState =
  | { mode: 'add'; provider: ProviderGroup }
  | { mode: 'edit'; model: AiModelMeta; provider: ProviderGroup };

export function AiModelConfigPanel({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const [config, setConfig] = useState<AiConfig | null>(null);
  const [modelCatalog, setModelCatalog] = useState<AiModelMeta[]>([]);
  const [providerMeta, setProviderMeta] = useState<AiProviderGroupMeta[]>([]);
  const [modelDialog, setModelDialog] = useState<ModelDialogState | null>(null);
  const [catalogBusy, setCatalogBusy] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AiModelMeta | null>(null);
  const [draftCatalogModel, setDraftCatalogModel] = useState<DraftCatalogModel>(EMPTY_DRAFT_MODEL);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [draftDefaultModel, setDraftDefaultModel] = useState('');
  const [draftKeys, setDraftKeys] = useState<Record<string, string>>({});
  const [draftBaseUrls, setDraftBaseUrls] = useState<Record<string, string>>({});
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({});
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});
  const testTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const testGeneration = useRef(0);

  useEffect(() => {
    const timers = testTimers.current;
    return () => {
      testGeneration.current += 1;
      Object.values(timers).forEach(clearTimeout);
    };
  }, []);

  const clearTestResults = useCallback((): void => {
    testGeneration.current += 1;
    Object.keys(testTimers.current).forEach(modelId => {
      clearTimeout(testTimers.current[modelId]);
      delete testTimers.current[modelId];
    });
    setTestResults({});
  }, []);

  const finishTest = (modelId: string, result: TestResult): void => {
    clearTimeout(testTimers.current[modelId]);
    setTestResults(prev => ({ ...prev, [modelId]: result }));
    testTimers.current[modelId] = setTimeout(() => {
      setTestResults(prev => ({ ...prev, [modelId]: { status: 'idle' } }));
      delete testTimers.current[modelId];
    }, TEST_RESULT_DURATION_MS);
  };

  const bridge = typeof window !== 'undefined' ? (window.rpaBridge ?? null) : null;

  const applyModelsResult = useCallback((result: AiModelsResult): void => {
    if (!result || !Array.isArray(result.models) || !Array.isArray(result.providers)) {
      throw new Error('模型目录响应缺少 models 或 providers');
    }
    setModelCatalog(result.models.filter(model => !model.custom));
    setProviderMeta(result.providers);
    clearTestResults();
  }, [clearTestResults]);


  const load = useCallback(async () => {
    try {
      const cfg = await (async (): Promise<AiConfig> => {
        if (!electron.available || !bridge) return await backend.getAiConfig();
        const result = await bridge.getAiConfig();
        if (!result.ok || !result.data) throw new Error(result.error ?? 'AI 配置读取失败');
        return result.data;
      })();
      if (cfg) {
        // config 是「后端存了哪些密钥」的唯一来源：漏掉它，storedValue 恒为空，
        // 已配置的服务商会显示成未配置，测试连接也拿不到密钥。
        setConfig(cfg);
        setDraftBaseUrls(cfg.base_urls ?? {});
        setDraftDefaultModel(cfg.default_model);
      }
      const models = await (async (): Promise<AiModelsResult> => {
        if (!electron.available || !bridge) return await backend.listAiModels();
        const result = await bridge.listAiModels();
        if (!result.ok || !result.data) throw new Error(result.error ?? '模型目录读取失败');
        return result.data;
      })();
      applyModelsResult(models);
      setLoadError(null);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'AI 配置读取失败');
    } finally {
      setLoading(false);
    }
  }, [applyModelsResult, bridge, electron.available]);

  // 误报：load 里 setState 全在 await 之后，规则只看回调体内有无 setState，不区分 await 边界
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);

  const handleSave = async (): Promise<void> => {
    if (Object.values(draftKeys).some(value => value.includes('****'))) {
      electron.pushToast('error', '新密钥不能包含连续四个星号');
      return;
    }
    setSaving(true);
    try {
      const payload = {
        // 空字符串表示清除该密钥，必须发给后端。
        api_keys: draftKeys,
        base_urls: draftBaseUrls,
        default_model: draftDefaultModel,
      };
      let updated: AiConfig | undefined;
      if (electron.available && bridge) {
        const res = await bridge.setAiConfig(payload);
        if (!res.ok || !res.data) throw new Error(res.error ?? 'AI 配置保存失败');
        updated = res.data;
      } else {
        updated = await backend.setAiConfig(payload);
      }
      if (updated) {
        setConfig(updated);
        setDraftKeys({});
        setDraftBaseUrls(updated.base_urls ?? {});
        setDraftDefaultModel(updated.default_model);
      }
      electron.pushToast('success', 'AI 配置已保存');
    } catch (error) {
      electron.pushToast('error', error instanceof Error ? error.message : 'AI 配置保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleTestModel = async (provider: ProviderGroup, modelId: string): Promise<void> => {
    const envKey = provider.env_key;
    const generation = testGeneration.current;
    clearTimeout(testTimers.current[modelId]);
    setTestResults(prev => ({ ...prev, [modelId]: { status: 'testing' } }));
    try {
      const payload = {
        env_key: envKey,
        model: modelId,
        // 只发明文草稿。后端读到的 config.api_keys 是掩码（sk-1****abcd），
        // 把它当密钥发回去，等于拿一串星号去鉴权；留空则由后端用已存的那把测。
        api_key: draftKeys[envKey] ?? '',
        base_url: draftBaseUrls[envKey] ?? '',
      };
      const data = await (async (): Promise<AiModelTestResult> => {
        if (electron.available && bridge) {
          const result = await bridge.testAiModel(payload);
          if (!result.ok) throw new Error(result.error ?? '模型测试失败');
          return result.data ?? { ok: false, error: '模型测试无返回' };
        }
        return await backend.testAiModel(payload);
      })();
      const result: TestResult = data.ok
        ? { status: 'ok', latencyMs: data.latency_ms, servedBy: data.served_by ?? undefined }
        : { status: 'fail', error: data.error };
      if (generation === testGeneration.current) finishTest(modelId, result);
      // 这里刻意不重新拉配置：测试连接不写盘，没有新状态可读回，
      // 而 load() 会用已存值覆盖 draftBaseUrls —— 刚输入还没保存的 Base URL 会被抹掉。
    } catch (err) {
      const message = err instanceof TypeError && String(err.message).includes('fetch')
        ? '无法连接后端服务，请检查后端是否已启动'
        : String(err);
      if (generation === testGeneration.current) finishTest(modelId, { status: 'fail', error: message });
    }
  };

  const handleStartAddModel = (provider: ProviderGroup): void => {
    setDraftCatalogModel(EMPTY_DRAFT_MODEL);
    setModelDialog({ mode: 'add', provider });
  };

  const handleStartEditModel = (model: AiModelMeta): void => {
    const provider = providerGroups.find(g => g.key === model.provider);
    if (!provider) return;
    setDraftCatalogModel({
      context_window: String(model.context_window ?? ''),
      id: model.id,
      label: model.label,
      recommended: model.recommended ?? false,
      tier: model.tier ?? 'standard',
    });
    setModelDialog({ mode: 'edit', model, provider });
  };

  const handleSubmitModelDialog = async (): Promise<void> => {
    if (!modelDialog) return;
    const contextWindowText = draftCatalogModel.context_window.trim();
    const contextWindow = Number(contextWindowText);
    if (!/^\d+$/.test(contextWindowText) || !Number.isSafeInteger(contextWindow)) {
      electron.pushToast('error', '上下文长度必须是非负整数');
      return;
    }

    if (modelDialog.mode === 'add') {
      const modelId = draftCatalogModel.id.trim();
      if (!modelId) {
        electron.pushToast('error', '模型 ID 不能为空');
        return;
      }
      setCatalogBusy(`add:${modelDialog.provider.key}`);
      try {
        const payload = {
          id: modelId,
          label: draftCatalogModel.label.trim() || modelId,
          provider: modelDialog.provider.key,
          env_key: modelDialog.provider.env_key,
          context_window: contextWindow,
          tier: draftCatalogModel.tier || 'standard',
          recommended: draftCatalogModel.recommended,
        };
        const refreshed = await (async (): Promise<AiModelsResult> => {
          if (electron.available && bridge) {
            const result = await bridge.addAiModel(payload);
            if (!result.ok || !result.data) throw new Error(result.error ?? '模型添加失败');
            return result.data;
          }
          return await backend.addAiModel(payload);
        })();
        applyModelsResult(refreshed);
        setModelDialog(null);
        electron.pushToast('success', '模型已添加');
      } catch (error) {
        electron.pushToast('error', error instanceof Error ? error.message : '模型添加失败');
      } finally {
        setCatalogBusy(null);
      }
      return;
    }

    const { model } = modelDialog;
    setCatalogBusy(`edit:${model.id}`);
    try {
      const payload = {
        id: model.id,
        label: draftCatalogModel.label.trim() || model.id,
        context_window: contextWindow,
        tier: draftCatalogModel.tier || 'standard',
        recommended: draftCatalogModel.recommended,
      };
      const refreshed = await (async (): Promise<AiModelsResult> => {
        if (electron.available && bridge) {
          const result = await bridge.updateAiModel(payload);
          if (!result.ok || !result.data) throw new Error(result.error ?? '模型更新失败');
          return result.data;
        }
        return await backend.updateAiModel(payload);
      })();
      applyModelsResult(refreshed);
      setModelDialog(null);
      electron.pushToast('success', '模型已更新');
    } catch (error) {
      electron.pushToast('error', error instanceof Error ? error.message : '模型更新失败');
    } finally {
      setCatalogBusy(null);
    }
  };

  const handleDeleteModel = async (model: AiModelMeta): Promise<void> => {
    setCatalogBusy(`delete:${model.id}`);
    try {
      const refreshed = await (async (): Promise<AiModelsResult> => {
        if (electron.available && bridge) {
          const result = await bridge.deleteAiModel(model.id);
          if (!result.ok || !result.data) throw new Error(result.error ?? '模型删除失败');
          return result.data;
        }
        return await backend.deleteAiModel(model.id);
      })();
      applyModelsResult(refreshed);
      if (refreshed.default) {
        setDraftDefaultModel(refreshed.default);
        setConfig((current) => current === null ? current : { ...current, default_model: refreshed.default });
      }
      setDeleteTarget(null);
      electron.pushToast('success', '模型已删除');
    } catch (error) {
      electron.pushToast('error', error instanceof Error ? error.message : '模型删除失败');
    } finally {
      setCatalogBusy(null);
    }
  };

  const providerGroups = toProviderGroups(providerMeta);
  const hasChanges = config !== null && (
    Object.entries(draftKeys).some(([envKey, value]) => value !== '' || Boolean(config.api_keys[envKey]))
    || draftDefaultModel !== config.default_model
    || JSON.stringify(draftBaseUrls) !== JSON.stringify(config.base_urls ?? {})
  );

  const configuredCount = providerGroups.filter(g => !!config?.api_keys[g.env_key]).length;
  const catalogCount = modelCatalog.length;

  const getTestResult = (modelId: string): TestResult =>
    testResults[modelId] ?? { status: 'idle' };

  return (
    <SettingsContent
      action={
        loading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-ink-4" />
        ) : (
          <span className="font-mono text-[11px] tabular-nums text-ink-3">
            已配置 {configuredCount}/{providerGroups.length} · 模型 {catalogCount}{hasChanges ? ' · 未保存' : ''}
          </span>
        )
      }
      icon={<Bot className="h-3.5 w-3.5" strokeWidth={1.5} />}
      title="AI 模型配置"
    >
      <div className="@container grid max-w-300 gap-6">
        {loadError !== null && (
          <SettingsLoadError
            message={loadError}
            onRetry={() => {
              setLoading(true);
              setLoadError(null);
              void load();
            }}
          />
        )}
        {config !== null && modelCatalog.length > 0 && (
          <div className="grid gap-1.5">
            <label className="text-[11px] font-medium text-ink-2" htmlFor="default-ai-model">默认 AI 模型</label>
            <select
              className={aiFieldClass}
              disabled={saving}
              id="default-ai-model"
              onChange={(event) => setDraftDefaultModel(event.target.value)}
              value={draftDefaultModel}
            >
              {modelCatalog.map((model) => (
                <option key={model.id} value={model.id}>{model.label} · {model.id}</option>
              ))}
            </select>
            <p className="text-[11px] text-ink-3">用于未指定模型的 AI 对话，以及定时任务失败后的自动诊断。</p>
          </div>
        )}
        <div className="grid gap-2">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[11px] font-medium text-ink-2">服务商密钥</span>
            <span className="text-[11px] text-ink-3">Base URL 留空时使用默认接口</span>
          </div>
          <div className="grid gap-2">
            {providerGroups.map(g => {
              const storedValue = config?.api_keys[g.env_key] ?? '';
              const draftValue = draftKeys[g.env_key];
              // 输入框永不预填 storedValue：它是掩码（sk-1****abcd），一旦落进 value，
              // 用户在后面补两个字符就得到 sk-1****abcdXY —— 含掩码的串会被保存逻辑静默丢弃，
              // 界面看着填了、点保存没报错、密钥其实没变。掩码只作占位提示出现。
              const displayValue = draftValue ?? '';
              const isConfigured = draftValue !== undefined ? draftValue !== '' : storedValue !== '';
              const isVisible = showKeys[g.env_key] ?? false;
              const providerModels = modelCatalog.filter(model => model.provider === g.key);

              return (
                <Collapsible
                  badge={
                    <div className="flex items-center gap-1.5">
                      <ProviderStatusBadge configured={storedValue !== ''} dirty={draftValue !== undefined && (draftValue !== '' || storedValue !== '')} />
                      <span className="rounded bg-paper-sunk px-1.5 py-0.5 font-mono text-[10px] text-ink-3">
                        {providerModels.length}
                      </span>
                    </div>
                  }
                  className="rounded-md border-rule bg-surface [&>button]:min-h-10 [&>button]:px-3 [&>button]:py-2 [&>div>div]:px-3 [&>div>div]:pb-3 [&>div>div]:pt-2.5"
                  defaultOpen={false}
                  key={g.env_key}
                  title={
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-[11px] font-medium text-ink-2">{g.label}</span>
                      <span className="hidden truncate font-mono text-[10px] font-normal text-ink-3 sm:inline">
                        {g.env_key}
                      </span>
                    </span>
                  }
                >
                  <section className="grid gap-2">
                    <div className={cn('grid gap-2 @min-3xl:items-start', aiProviderGridClass)}>
                      <div className="flex min-h-7 items-center text-[11px] text-ink-3">
                        密钥配置
                      </div>

                      <div className="grid min-w-0 gap-1">
                        <div className="relative min-w-0">
                          <input
                            aria-label={`${g.label} API Key`}
                            className={cn(aiMonoFieldClass, 'pr-8')}
                            disabled={saving}
                            placeholder={storedValue && draftValue === undefined ? `已配置 ${storedValue}` : g.placeholder}
                            type={isVisible ? 'text' : 'password'}
                            value={displayValue}
                            onChange={e => { setDraftKeys(prev => ({ ...prev, [g.env_key]: e.target.value })); clearTestResults(); }}
                          />
                          <button
                            className="absolute right-1.5 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded text-ink-4 transition-colors hover:bg-paper-sunk hover:text-ink"
                            disabled={saving || !displayValue}
                            onClick={() => setShowKeys(prev => ({ ...prev, [g.env_key]: !prev[g.env_key] }))}
                            title={isVisible ? '隐藏新密钥' : '显示新密钥'}
                            type="button"
                          >
                            {isVisible ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                          </button>
                        </div>
                        {storedValue && draftValue === undefined && (
                          <button className="w-fit text-[10px] text-ink-3 underline-offset-2 hover:text-ink-2 hover:underline" disabled={saving} onClick={() => { setDraftKeys(prev => ({ ...prev, [g.env_key]: '' })); clearTestResults(); }} type="button">
                            清除已保存密钥
                          </button>
                        )}
                        {storedValue && draftValue === '' && <p className="text-[10px] text-amber-800">保存后将移除密钥。</p>}
                      </div>

                      <input
                        aria-label={`${g.label} Base URL`}
                        className={aiMonoFieldClass}
                        disabled={saving}
                        placeholder="Base URL"
                        type="text"
                        value={draftBaseUrls[g.env_key] ?? ''}
                        onChange={e => { setDraftBaseUrls(prev => ({ ...prev, [g.env_key]: e.target.value })); clearTestResults(); }}
                      />

                      <div className="flex h-7 items-center justify-end gap-1 @min-3xl:gap-0.5 @min-3xl:rounded-md @min-3xl:bg-surface @min-3xl:p-0.5">
                        {g.docsUrl !== '' && (
                          <IconButton
                            className="h-6 w-6 text-ink-4 hover:text-ink"
                            label={`打开 ${g.label} API Key 页面`}
                            render={<a href={g.docsUrl} rel="noreferrer" target="_blank" />}
                            variant="ghost"
                          >
                            <ExternalLink className="h-3.5 w-3.5" strokeWidth={1.5} />
                          </IconButton>
                        )}
                        <IconButton
                          active={modelDialog?.mode === 'add' && modelDialog.provider.key === g.key}
                          className="h-6 w-6 text-ink-4 hover:text-ink"
                          label="新增模型"
                          onClick={() => handleStartAddModel(g)}
                        >
                          <Plus className="h-3.5 w-3.5" strokeWidth={1.5} />
                        </IconButton>
                      </div>
                    </div>

                    {providerModels.length > 0 && (
                      <div className={cn('grid items-start gap-2 border-t border-rule pt-2.5', aiProviderGridClass)}>
                        <div className="flex h-7 items-center text-[11px] text-ink-3">
                          本地模型
                        </div>
                        <div className="min-w-0 @min-3xl:col-span-3">
                          <div className="flex flex-wrap gap-1.5">
                            {providerModels.map(model => (
                              <ManagedModelTag
                                busy={catalogBusy === `delete:${model.id}`}
                                key={model.id}
                                model={model}
                                testDisabled={!isConfigured && !(draftBaseUrls[g.env_key] ?? '').trim()}
                                testResult={getTestResult(model.id)}
                                onDelete={() => setDeleteTarget(model)}
                                onEdit={() => handleStartEditModel(model)}
                                onTest={() => void handleTestModel(g, model.id)}
                              />
                            ))}
                          </div>
                          {providerModels.map(model => {
                            const mt = getTestResult(model.id);
                            if (mt.status === 'ok') return (
                              <p className="mt-1.5 rounded-md bg-emerald-50/70 px-2.5 py-1.5 font-mono text-[11px] leading-snug text-emerald-700" key={model.id} role="status">
                                <span className="font-semibold">{model.id}</span>：连接正常{mt.latencyMs !== undefined ? ` · ${mt.latencyMs}ms` : ''}{mt.servedBy ? ` · 实际由 ${mt.servedBy} 应答` : ''}
                              </p>
                            );
                            if (mt.status !== 'fail' || !mt.error) return null;
                            return (
                              <p
                                className="mt-1.5 wrap-break-word rounded-md bg-red-50/70 px-2.5 py-1.5 font-mono text-[11px] leading-snug text-red-700"
                                key={model.id}
                              >
                                <span className="font-semibold">{model.id}</span>：{mt.error}
                              </p>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </section>
                </Collapsible>
              );
            })}
          </div>
        </div>
      </div>

      <div className="flex items-center justify-end gap-3 pt-3">
        <Button
          className="h-8 rounded-md px-4 text-[11px]"
          disabled={saving || loading || !hasChanges}
          onClick={() => void handleSave()}
          variant="subtle"
        >
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {saving ? '保存中…' : '保存配置'}
        </Button>
      </div>
      <DeleteModelConfirmDialog
        busy={deleteTarget !== null && catalogBusy === `delete:${deleteTarget.id}`}
        model={deleteTarget}
        onConfirm={() => {
          if (deleteTarget !== null) void handleDeleteModel(deleteTarget);
        }}
        onOpenChange={(open) => {
          if (!open && catalogBusy === null) setDeleteTarget(null);
        }}
      />
      <CatalogModelDialog
        busy={
          modelDialog !== null
          && (modelDialog.mode === 'add'
            ? catalogBusy === `add:${modelDialog.provider.key}`
            : catalogBusy === `edit:${modelDialog.model.id}`)
        }
        dialogState={modelDialog}
        draft={draftCatalogModel}
        onChange={setDraftCatalogModel}
        onOpenChange={(open) => { if (!open) setModelDialog(null); }}
        onSubmit={() => void handleSubmitModelDialog()}
      />
    </SettingsContent>
  );
}

function ProviderStatusBadge({ configured, dirty }: { configured: boolean; dirty: boolean }): ReactElement {
  return (
    <span
      className={cn(
        'inline-flex h-4 shrink-0 items-center rounded px-1.5 text-[10px] font-medium',
        dirty
          ? 'border border-amber-200 bg-amber-50 text-amber-800'
          : configured
          ? 'bg-emerald-50 text-emerald-700'
          : 'border border-rule bg-paper-sunk text-ink-4',
      )}
    >
      {dirty ? '未保存' : configured ? '已配置' : '未配置'}
    </span>
  );
}

function ManagedModelTag({
  busy,
  model,
  onDelete,
  onEdit,
  onTest,
  testDisabled,
  testResult,
}: {
  busy: boolean;
  model: AiModelMeta;
  onDelete: () => void;
  onEdit: () => void;
  onTest: () => void;
  testDisabled: boolean;
  testResult: TestResult;
}): ReactElement {
  const context = model.context_window > 0 ? `${Math.round(model.context_window / 1000)}k` : null;
  const title = `${model.label} · ${model.id}${context ? ` · ${context}` : ''} · 点击编辑`;
  const ts = testResult.status;
  const testTitle = (() => {
    if (testDisabled) return '先填写该服务商的 API Key 或 Base URL';
    if (ts === 'testing') return '测试连接中';
    if (ts === 'ok') {
      const latency = testResult.latencyMs ? `，${testResult.latencyMs}ms` : '';
      // 中转按 family 模糊匹配时答话的是另一个模型，不说出来「连接正常」就是假的
      return testResult.servedBy
        ? `连接正常${latency}（实际由 ${testResult.servedBy} 应答）`
        : `连接正常${latency}`;
    }
    if (ts === 'fail') return testResult.error ? `连接失败：${testResult.error}` : '连接失败';
    return `测试 ${model.id} 连接`;
  })();
  return (
    <span
      className={cn(
        'inline-flex h-7 min-w-0 max-w-full items-center overflow-hidden rounded-md border bg-paper-sunk/70 text-[11px] text-ink-2',
        ts === 'ok' ? 'border-emerald-200' : ts === 'fail' ? 'border-red-200' : 'border-rule',
      )}
      title={title}
    >
      <button
        className="min-w-0 truncate px-2 py-0 text-left transition-colors hover:text-accent-strong"
        onClick={onEdit}
        type="button"
      >
        <span className="font-medium">{model.label}</span>
        <span className="ml-1 font-mono text-ink-3">{model.id}</span>
      </button>
      {model.recommended && (
        <span className="border-l border-rule px-1.5 text-[10px] text-accent-strong">推荐</span>
      )}
      {ts === 'ok' && testResult.servedBy && (
        <span className="border-l border-rule px-1.5 font-mono text-[10px] text-amber-800">
          ↦{testResult.servedBy}
        </span>
      )}
      <button
        className={cn(
          'flex h-7 w-7 shrink-0 items-center justify-center border-l border-rule transition-colors disabled:opacity-50',
          ts === 'ok' && 'text-emerald-600 hover:bg-emerald-50',
          ts === 'fail' && 'text-red-500 hover:bg-red-50',
          ts !== 'ok' && ts !== 'fail' && 'text-ink-4 hover:bg-surface hover:text-ink',
        )}
        disabled={testDisabled || ts === 'testing'}
        onClick={onTest}
        title={testTitle}
        type="button"
      >
        {ts === 'testing' && <Loader2 className="h-3 w-3 animate-spin" />}
        {ts === 'fail' && <WifiOff className="h-3 w-3" />}
        {ts !== 'testing' && ts !== 'fail' && <Wifi className="h-3 w-3" />}
      </button>
      <button
        className="flex h-7 w-7 shrink-0 items-center justify-center border-l border-rule text-ink-4 transition-colors hover:bg-red-50 hover:text-red-500 disabled:opacity-50"
        disabled={busy}
        onClick={onDelete}
        title="删除模型"
        type="button"
      >
        {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
      </button>
    </span>
  );
}

function DeleteModelConfirmDialog({
  busy,
  model,
  onConfirm,
  onOpenChange,
}: {
  busy: boolean;
  model: AiModelMeta | null;
  onConfirm: () => void;
  onOpenChange: (open: boolean) => void;
}): ReactElement {
  return (
    <AlertDialog onOpenChange={onOpenChange} open={model !== null}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除本地模型</AlertDialogTitle>
          <AlertDialogDescription>
            将从 backend/config/model_catalog.json 删除「{model?.label ?? '当前模型'}」。
            删除后该模型不会再出现在模型选择列表中。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="rounded-md border border-rule bg-paper-sunk px-3 py-2 font-mono text-[11px] text-ink-3">
          {model?.id ?? '--'}
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
          <AlertDialogAction disabled={busy} onClick={onConfirm}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" strokeWidth={1.5} />}
            删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function CatalogModelDialog({
  busy,
  dialogState,
  draft,
  onChange,
  onOpenChange,
  onSubmit,
}: {
  busy: boolean;
  dialogState: ModelDialogState | null;
  draft: DraftCatalogModel;
  onChange: (draft: DraftCatalogModel) => void;
  onOpenChange: (open: boolean) => void;
  onSubmit: () => void;
}): ReactElement {
  const isEdit = dialogState?.mode === 'edit';
  return (
    <Dialog onOpenChange={onOpenChange} open={dialogState !== null}>
      <DialogContent className="w-120">
        <DialogHeader>
          <DialogTitle>{isEdit ? '编辑模型' : '新增模型'}</DialogTitle>
          <DialogDescription>
            {dialogState?.provider.label ?? ''}
            {isEdit ? ' · 修改本地模型' : ' · 添加本地模型'}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="grid gap-3">
          <div className="grid gap-3 @min-3xl:grid-cols-2">
            <ModelField label="模型 ID">
              {isEdit ? (
                <div className={cn(aiMonoFieldClass, 'flex items-center bg-paper-sunk text-ink-3')}>
                  {draft.id}
                </div>
              ) : (
                <input
                  className={aiMonoFieldClass}
                  placeholder="如 openai/qwen3-32b"
                  value={draft.id}
                  onChange={event => onChange({ ...draft, id: event.target.value })}
                />
              )}
            </ModelField>
            <ModelField label="显示名称（可选）">
              <input
                className={aiFieldClass}
                placeholder="留空则使用模型 ID"
                value={draft.label}
                onChange={event => onChange({ ...draft, label: event.target.value })}
              />
            </ModelField>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <ModelField label="上下文长度">
              <input
                className={aiMonoFieldClass}
                inputMode="numeric"
                placeholder="如 200000"
                value={draft.context_window}
                onChange={event => onChange({ ...draft, context_window: event.target.value })}
              />
            </ModelField>
            <ModelField label="模型等级">
              <select
                className={aiFieldClass}
                value={draft.tier}
                onChange={event => onChange({ ...draft, tier: event.target.value })}
              >
                <option value="weak">快速</option>
                <option value="standard">标准</option>
                <option value="strong">强模型</option>
              </select>
            </ModelField>
          </div>
          <label className="flex items-center gap-1.5 text-[11px] text-ink-3">
            <Checkbox
              checked={draft.recommended}
              onCheckedChange={checked => onChange({ ...draft, recommended: checked === true })}
            />
            设为推荐模型
          </label>
        </DialogBody>
        <DialogFooter>
          <Button className="h-7 px-2 text-[11px] text-ink-3" disabled={busy} onClick={() => onOpenChange(false)} variant="ghost">
            取消
          </Button>
          <Button className="h-7 px-2.5 text-[11px]" disabled={busy} onClick={onSubmit} variant="outline">
            {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
            {isEdit ? '保存' : '添加'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ModelField({ className, label, children }: { className?: string; label: string; children: ReactElement }): ReactElement {
  return (
    <label className={cn('grid min-w-0 gap-1', className)}>
      <span className="text-[10px] text-ink-3">{label}</span>
      {children}
    </label>
  );
}
