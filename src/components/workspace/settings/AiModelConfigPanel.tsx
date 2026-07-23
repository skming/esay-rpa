import {
  Bot, ExternalLink, Eye, EyeOff, Loader2, Plus, Trash2, Wifi, WifiOff,
} from 'lucide-react';
import type { ReactElement } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { backend } from '../../../lib/backendClient';
import { cn } from '../../../lib/utils';
import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import type { AiConfig, AiModelMeta, AiModelTestResult } from '../../../types/electron';
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
import { SettingsContent } from './SettingsContent';


type ProviderGroup = { key: string; label: string; env_key: string; placeholder: string; docsUrl: string };

// 只放目录里没有、也不该由目录承载的取密钥入口；厂商本身来自目录
const PROVIDER_HINTS: Record<string, { placeholder: string; docsUrl: string }> = {
  anthropic: { placeholder: 'sk-ant-api03-…', docsUrl: 'https://console.anthropic.com/settings/keys' },
  openai: { placeholder: 'sk-proj-…', docsUrl: 'https://platform.openai.com/api-keys' },
  google: { placeholder: 'AIza…', docsUrl: 'https://aistudio.google.com/app/apikey' },
  deepseek: { placeholder: 'sk-…', docsUrl: 'https://platform.deepseek.com/api_keys' },
  qwen: { placeholder: 'sk-…', docsUrl: 'https://dashscope.console.aliyun.com/apiKey' },
  zai: { placeholder: '…', docsUrl: 'https://z.ai/manage-apikey/apikey-list' },
  xai: { placeholder: 'xai-…', docsUrl: 'https://console.x.ai/' },
};

/** 厂商分组由模型目录推导。写死一份等于第二套事实来源——新厂商进了目录，
 *  选择器里能选、设置页却没有它的 API Key 输入框，模型永远是「未配置」。 */
function deriveProviderGroups(catalog: AiModelMeta[]): ProviderGroup[] {
  const groups = new Map<string, ProviderGroup>();
  for (const model of catalog) {
    if (!model.provider || !model.env_key || groups.has(model.provider)) continue;
    groups.set(model.provider, {
      key: model.provider,
      label: model.provider_label ?? model.provider,
      env_key: model.env_key,
      ...(PROVIDER_HINTS[model.provider] ?? { placeholder: '…', docsUrl: '' }),
    });
  }
  return [...groups.values()];
}

type TestStatus = 'idle' | 'testing' | 'ok' | 'fail';
type TestResult = { status: TestStatus; latencyMs?: number; error?: string };

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
  const [modelDialog, setModelDialog] = useState<ModelDialogState | null>(null);
  const [catalogBusy, setCatalogBusy] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AiModelMeta | null>(null);
  const [draftCatalogModel, setDraftCatalogModel] = useState<DraftCatalogModel>(EMPTY_DRAFT_MODEL);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [draftKeys, setDraftKeys] = useState<Record<string, string>>({});
  const [draftBaseUrls, setDraftBaseUrls] = useState<Record<string, string>>({});
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({});
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});
  const testTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const bridge = typeof window !== 'undefined' ? (window.rpaBridge ?? null) : null;

  const applyModelsResult = (models: AiModelMeta[] | undefined): void => {
    if (Array.isArray(models)) {
      setModelCatalog(models.filter(model => !model.custom));
    }
  };


  // 不在开头 setLoading(true)：loading 初值即为 true，而模型测试后的那次刷新是后台刷新，
  // 不该把整个面板打回骨架屏
  const load = useCallback(async () => {
    try {
      const cfg = electron.available && bridge
        ? (await bridge.getAiConfig()).data
        : await backend.getAiConfig();
      if (cfg) {
        setConfig(cfg);
        setDraftBaseUrls(cfg.base_urls ?? {});
      }
      applyModelsResult(electron.available && bridge
        ? (await bridge.listAiModels()).data?.models
        : (await backend.listAiModels()).models);
    } catch {
      // 配置读取失败不阻断设置页渲染，用户仍可通过保存操作重试。
    } finally {
      setLoading(false);
    }
  }, [bridge, electron.available]);

  // 误报：load 里 setState 全在 await 之后，规则只看回调体内有无 setState，不区分 await 边界
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);

  const handleSave = async (): Promise<void> => {
    setSaving(true);
    try {
      const payload = {
        // 空字符串表示"清除该密钥"，必须发给后端；只过滤含掩码的中间态输入。
        api_keys: Object.fromEntries(Object.entries(draftKeys).filter(([, v]) => !v.includes('****'))),
        base_urls: draftBaseUrls,
      };
      let updated: AiConfig | undefined;
      if (electron.available && bridge) {
        const res = await bridge.setAiConfig(payload);
        updated = res.data;
      } else {
        updated = await backend.setAiConfig(payload);
      }
      if (updated) {
        setConfig(updated);
        setDraftKeys({});
      }
      electron.pushToast('success', 'AI 配置已保存');
    } catch {
      electron.pushToast('error', 'AI 配置保存失败，请检查后端服务');
    } finally {
      setSaving(false);
    }
  };

  const handleTestKey = async (envKey: string): Promise<void> => {
    setTestResults(prev => ({ ...prev, [envKey]: { status: 'testing' } }));
    if (testTimers.current[envKey]) clearTimeout(testTimers.current[envKey]);
    try {
      const payload = {
        env_key: envKey,
        api_key: draftKeys[envKey] ?? config?.api_keys[envKey] ?? '',
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
        ? { status: 'ok', latencyMs: data.latency_ms }
        : { status: 'fail', error: data.error };
      setTestResults(prev => ({ ...prev, [envKey]: result }));
      void load();
      testTimers.current[envKey] = setTimeout(
        () => setTestResults(prev => ({ ...prev, [envKey]: { status: 'idle' } })),
        5000
      );
    } catch (err) {
      const message = err instanceof TypeError && String(err.message).includes('fetch')
        ? '无法连接后端服务，请检查后端是否已启动'
        : String(err);
      setTestResults(prev => ({ ...prev, [envKey]: { status: 'fail', error: message } }));
      testTimers.current[envKey] = setTimeout(
        () => setTestResults(prev => ({ ...prev, [envKey]: { status: 'idle' } })),
        5000
      );
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
    const contextWindow = Number.parseInt(draftCatalogModel.context_window, 10);
    if (!Number.isFinite(contextWindow) || contextWindow < 0) {
      electron.pushToast('error', '上下文长度必须是非负数字');
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
        const models = await (async (): Promise<AiModelMeta[] | undefined> => {
          if (electron.available && bridge) {
            const result = await bridge.addAiModel(payload);
            if (!result.ok) throw new Error(result.error ?? '模型添加失败');
            return result.data?.models;
          }
          return (await backend.addAiModel(payload)).models;
        })();
        applyModelsResult(models);
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
      const models = await (async (): Promise<AiModelMeta[] | undefined> => {
        if (electron.available && bridge) {
          const result = await bridge.updateAiModel(payload);
          if (!result.ok) throw new Error(result.error ?? '模型更新失败');
          return result.data?.models;
        }
        return (await backend.updateAiModel(payload)).models;
      })();
      applyModelsResult(models);
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
      const models = await (async (): Promise<AiModelMeta[] | undefined> => {
        if (electron.available && bridge) {
          const result = await bridge.deleteAiModel(model.id);
          if (!result.ok) throw new Error(result.error ?? '模型删除失败');
          return result.data?.models;
        }
        return (await backend.deleteAiModel(model.id)).models;
      })();
      applyModelsResult(models);
      setDeleteTarget(null);
      electron.pushToast('success', '模型已删除');
    } catch (error) {
      electron.pushToast('error', error instanceof Error ? error.message : '模型删除失败');
    } finally {
      setCatalogBusy(null);
    }
  };

  const providerGroups = deriveProviderGroups(modelCatalog);

  const configuredCount = providerGroups.filter(g => {
    const draft = draftKeys[g.env_key];
    return draft !== undefined ? draft !== '' : !!(config?.api_keys[g.env_key]);
  }).length;
  const catalogCount = modelCatalog.length;

  const getTestResult = (envKey: string): TestResult =>
    testResults[envKey] ?? { status: 'idle' };

  return (
    <SettingsContent
      action={
        loading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-ink-4" />
        ) : (
          <span className="font-mono text-[11px] tabular-nums text-ink-3">
            已配置 {configuredCount}/{providerGroups.length} · 模型 {catalogCount}
          </span>
        )
      }
      icon={<Bot className="h-3.5 w-3.5" strokeWidth={1.5} />}
      title="AI 模型配置"
    >
      <div className="@container grid max-w-300 gap-6">
        <div className="grid gap-2">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[11px] font-medium text-ink-2">服务商密钥</span>
            <span className="text-[11px] text-ink-3">Base URL 留空时使用默认接口</span>
          </div>
          <div className="grid gap-2">
            {providerGroups.map(g => {
              const storedValue = config?.api_keys[g.env_key] ?? '';
              const draftValue = draftKeys[g.env_key];
              const displayValue = draftValue ?? storedValue;
              const isConfigured = draftValue !== undefined ? draftValue !== '' : storedValue !== '';
              const isVisible = showKeys[g.env_key] ?? false;
              const tr = getTestResult(g.env_key);
              const ts = tr.status;
              const providerModels = modelCatalog.filter(model => model.provider === g.key);

              return (
                <Collapsible
                  badge={
                    <div className="flex items-center gap-1.5">
                      <ProviderStatusBadge configured={isConfigured} />
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

                      <div className="min-w-0">
                        <input
                          className={cn(aiMonoFieldClass, 'pr-8')}
                          placeholder={g.placeholder}
                          type={isVisible ? 'text' : 'password'}
                          value={displayValue}
                          onChange={e => setDraftKeys(prev => ({ ...prev, [g.env_key]: e.target.value }))}
                        />
                        <button
                          className="absolute right-1.5 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded text-ink-4 transition-colors hover:bg-paper-sunk hover:text-ink"
                          onClick={() => setShowKeys(prev => ({ ...prev, [g.env_key]: !prev[g.env_key] }))}
                          title={isVisible ? '隐藏密钥' : '显示密钥'}
                          type="button"
                        >
                          {isVisible ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                        </button>
                      </div>

                      <input
                        className={aiMonoFieldClass}
                        placeholder="Base URL"
                        type="text"
                        value={draftBaseUrls[g.env_key] ?? ''}
                        onChange={e => setDraftBaseUrls(prev => ({ ...prev, [g.env_key]: e.target.value }))}
                      />

                      <div className="flex h-7 items-center justify-end gap-1 @min-3xl:gap-0.5 @min-3xl:rounded-md @min-3xl:bg-surface @min-3xl:p-0.5">
                        <TestModelButton
                          disabled={ts === 'testing' || (!isConfigured && !draftValue)}
                          result={tr}
                          onClick={() => void handleTestKey(g.env_key)}
                        />
                        <IconButton
                          className="h-6 w-6 text-ink-4 hover:text-ink"
                          label={`打开 ${g.label} API Key 页面`}
                          variant="ghost"
                          asChild
                        >
                          <a href={g.docsUrl} rel="noreferrer" target="_blank">
                            <ExternalLink className="h-3.5 w-3.5" strokeWidth={1.5} />
                          </a>
                        </IconButton>
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

                    {ts === 'fail' && tr.error && (
                      <p className="rounded-md bg-red-50/70 px-2.5 py-1.5 text-[11px] leading-snug text-red-600">
                        {tr.error}
                      </p>
                    )}

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
                                onDelete={() => setDeleteTarget(model)}
                                onEdit={() => handleStartEditModel(model)}
                              />
                            ))}
                          </div>
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
          disabled={saving || loading}
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

function ProviderStatusBadge({ configured }: { configured: boolean }): ReactElement {
  return (
    <span
      className={cn(
        'inline-flex h-4 shrink-0 items-center rounded px-1.5 text-[10px] font-medium',
        configured
          ? 'bg-emerald-50 text-emerald-700'
          : 'border border-rule bg-paper-sunk text-ink-4',
      )}
    >
      {configured ? '已配置' : '未配置'}
    </span>
  );
}

function ManagedModelTag({
  busy,
  model,
  onDelete,
  onEdit,
}: {
  busy: boolean;
  model: AiModelMeta;
  onDelete: () => void;
  onEdit: () => void;
}): ReactElement {
  const context = model.context_window > 0 ? `${Math.round(model.context_window / 1000)}k` : null;
  const title = `${model.label} · ${model.id}${context ? ` · ${context}` : ''} · 点击编辑`;
  return (
    <span
      className="inline-flex h-7 min-w-0 max-w-full items-center overflow-hidden rounded-md border border-rule bg-paper-sunk/70 text-[11px] text-ink-2"
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

function TestModelButton({
  disabled,
  onClick,
  result,
}: {
  disabled: boolean;
  onClick: () => void;
  result: TestResult;
}): ReactElement {
  const label = (() => {
    if (result.status === 'testing') return '测试连接中';
    if (result.status === 'ok') return `连接正常${result.latencyMs ? `，${result.latencyMs}ms` : ''}`;
    if (result.status === 'fail') return result.error ? `连接失败：${result.error}` : '连接失败';
    return '测试模型连接';
  })();

  return (
    <IconButton
      className={cn(
        'h-6 w-6 text-ink-4 hover:text-ink',
        result.status === 'ok' && 'text-emerald-600 hover:text-emerald-700',
        result.status === 'fail' && 'text-red-500 hover:bg-red-50 hover:text-red-600',
      )}
      disabled={disabled}
      label={label}
      onClick={onClick}
    >
      {result.status === 'testing' && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {result.status === 'ok' && <Wifi className="h-3.5 w-3.5" strokeWidth={1.5} />}
      {result.status === 'fail' && <WifiOff className="h-3.5 w-3.5" strokeWidth={1.5} />}
      {result.status === 'idle' && <Wifi className="h-3.5 w-3.5" strokeWidth={1.5} />}
    </IconButton>
  );
}
