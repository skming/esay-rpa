import { Bell, Eye, EyeOff, Loader2 } from 'lucide-react';
import type { ReactElement } from 'react';
import { useCallback, useEffect, useState } from 'react';
import { backend, type NotificationConfig } from '../../../lib/backendClient';
import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import { cn } from '../../../lib/utils';
import { Button } from '../../ui/button';
import { Collapsible } from '../../ui/collapsible';
import { Switch } from '../../ui/switch';
import { SettingsContent } from './SettingsContent';

const fieldClass = 'h-8 w-full rounded-md border border-rule-2 bg-surface px-2.5 text-[11px] text-ink-2 outline-none transition placeholder:text-ink-3 focus-visible:border-accent-line focus-visible:ring-2 focus-visible:ring-accent-soft';
const monoFieldClass = cn(fieldClass, 'font-mono');

export function NotificationConfigPanel({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const [config, setConfig] = useState<NotificationConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [webhookUrl, setWebhookUrl] = useState('');
  const [secretDraft, setSecretDraft] = useState<string | null>(null);
  const [secretVisible, setSecretVisible] = useState(false);

  // 不在开头 setLoading(true)：loading 初值就是 true，而本函数只在挂载时调用一次
  const load = useCallback(async () => {
    try {
      const cfg = await backend.getNotificationConfig();
      setConfig(cfg);
      setEnabled(cfg.dingtalk_enabled);
      setWebhookUrl(cfg.dingtalk_webhook_url);
      setSecretDraft(null);
    } catch {
      // 配置读取失败不阻断设置页渲染，用户仍可通过保存操作重试。
    } finally {
      setLoading(false);
    }
  }, []);

  // 误报：load 里 setState 全在 await 之后，规则只看回调体内有无 setState，不区分 await 边界
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);

  const handleSave = async (): Promise<void> => {
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        dingtalk_enabled: enabled,
        dingtalk_webhook_url: webhookUrl.trim(),
      };
      if (secretDraft !== null && !secretDraft.includes('****')) {
        payload.dingtalk_secret = secretDraft;
      }
      const updated = await backend.setNotificationConfig(payload);
      setConfig(updated);
      setSecretDraft(null);
      electron.pushToast('success', '通知配置已保存');
    } catch {
      // 保存失败时保留草稿，用户可直接重试
      electron.pushToast('error', '通知配置保存失败，请检查后端服务');
    } finally {
      setSaving(false);
    }
  };

  const secretValue = secretDraft ?? config?.dingtalk_secret ?? '';

  return (
    <SettingsContent
      action={loading ? <Loader2 className="h-3.5 w-3.5 animate-spin text-ink-4" /> : null}
      icon={<Bell className="h-3.5 w-3.5" strokeWidth={1.5} />}
      title="通知渠道"
    >
      <div className="grid w-full max-w-300 gap-4">
        <Collapsible
          badge={<NotificationStatusBadge configured={enabled && webhookUrl.trim() !== ''} />}
          defaultOpen
          title={
            <span className="flex min-w-0 items-center gap-2">
              <span className="truncate text-[11px] font-medium text-ink-2">钉钉自定义机器人</span>
            </span>
          }
        >
          <div className="grid gap-2.5">
            <div className="flex items-center justify-between gap-3">
              <p className="text-[11px] leading-snug text-ink-3">
                配置钉钉机器人 Webhook，在钉钉群 → 群设置 → 智能群助手 → 添加机器人 中获取。
              </p>
              <Switch checked={enabled} className="shrink-0" onCheckedChange={setEnabled} />
            </div>

            <div className="grid gap-1.5">
              <span className="text-[11px] text-ink-3">Webhook URL</span>
              <input
                className={monoFieldClass}
                onChange={e => setWebhookUrl(e.target.value)}
                placeholder="https://oapi.dingtalk.com/robot/send?access_token=…"
                type="text"
                value={webhookUrl}
              />
            </div>

            <div className="grid gap-1.5">
              <span className="text-[11px] text-ink-3">加签密钥（可选）</span>
              <div className="relative min-w-0">
                <input
                  className={cn(monoFieldClass, 'pr-8')}
                  onChange={e => setSecretDraft(e.target.value)}
                  onFocus={() => {
                    // 聚焦即清掩码，否则用户在 **** 上编辑出的值会被保存逻辑静默忽略
                    if (secretValue.includes('****')) setSecretDraft('');
                  }}
                  placeholder="SEC…"
                  type={secretVisible ? 'text' : 'password'}
                  value={secretValue}
                />
                <button
                  className="absolute right-1.5 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded text-ink-4 transition-colors hover:bg-paper-sunk hover:text-ink"
                  onClick={() => setSecretVisible(v => !v)}
                  title={secretVisible ? '隐藏密钥' : '显示密钥'}
                  type="button"
                >
                  {secretVisible ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                </button>
              </div>
            </div>
          </div>
        </Collapsible>
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
    </SettingsContent>
  );
}

function NotificationStatusBadge({ configured }: { configured: boolean }): ReactElement {
  return (
    <span
      className={cn(
        'inline-flex h-4 shrink-0 items-center rounded px-1.5 text-[10px] font-medium',
        configured
          ? 'bg-emerald-50 text-emerald-700'
          : 'border border-rule bg-paper-sunk text-ink-4',
      )}
    >
      {configured ? '已启用' : '未配置'}
    </span>
  );
}
