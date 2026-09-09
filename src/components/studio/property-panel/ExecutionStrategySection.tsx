import { Clock3, ShieldAlert } from 'lucide-react';
import type { ReactElement } from 'react';

import { shouldUseRequireConfirmation } from '../../../lib/nodeConfigDraft';
import type { RpaNodeConfigDraft } from '../../../types/rpa';
import { Button } from '../../ui/button';
import { NumberField, ToggleSwitch } from '../../ui/FormControls';
import { PanelSection } from './PanelSection';

type DraftPatch = <K extends keyof RpaNodeConfigDraft>(key: K, value: RpaNodeConfigDraft[K]) => void;

const TIMEOUT_PRESETS = [10, 30, 60, 300] as const;

export function ExecutionStrategySection({
  actionType,
  draft,
  isNoExec,
  onDraftPatch
}: {
  actionType: string;
  draft: RpaNodeConfigDraft;
  isNoExec: boolean;
  onDraftPatch: DraftPatch;
}): ReactElement | null {
  if (isNoExec) return null;

  const isBrowserLike = actionType.startsWith('browser.') || actionType.startsWith('ui.');
  const canCaptureAfter = isBrowserLike || actionType === 'browser.fetch';

  return (
    <PanelSection title="执行策略">
      <div className="rounded-lg border border-slate-200/70 bg-slate-50/70 p-2">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-1.5 text-[11px] font-medium text-slate-700">
            <Clock3 className="h-3.5 w-3.5 text-blue-500" strokeWidth={1.5} />
            <span>等待超时</span>
          </div>
          <span className="font-mono text-[10px] text-slate-500">{draft.timeoutSeconds}s</span>
        </div>
        <div className="grid grid-cols-4 gap-1">
          {TIMEOUT_PRESETS.map((seconds) => (
            <Button
              className="h-6 rounded-md px-0 font-mono text-[10px]"
              key={seconds}
              onClick={() => onDraftPatch('timeoutSeconds', seconds)}
              variant={draft.timeoutSeconds === seconds ? 'secondary' : 'outline'}
            >
              {seconds >= 60 ? `${seconds / 60}m` : `${seconds}s`}
            </Button>
          ))}
        </div>
        <div className="mt-2">
          <NumberField
            label="自定义秒数"
            onChange={(event) => onDraftPatch('timeoutSeconds', Math.max(1, Number.parseInt(event.target.value, 10) || 1))}
            onStep={(delta) => onDraftPatch('timeoutSeconds', Math.max(1, draft.timeoutSeconds + delta))}
            value={String(draft.timeoutSeconds)}
          />
        </div>
      </div>

      <div className="rounded-lg border border-slate-200/70 bg-white px-2 py-1.5">
        <ToggleSwitch
          checked={draft.continueOnError}
          label="可选步骤，失败继续"
          onCheckedChange={(checked) => onDraftPatch('continueOnError', checked)}
        />
        {canCaptureAfter && (
          <ToggleSwitch
            checked={draft.autoSave}
            label="执行后截图记录"
            onCheckedChange={(checked) => onDraftPatch('autoSave', checked)}
          />
        )}
        {/* 可见条件和 applyNodeConfigDraft 的写回条件必须同一个判据：错开就会出现勾了开关、
            闸门却没落进定义，而界面上看不出来。 */}
        {shouldUseRequireConfirmation(actionType) && (
          <ToggleSwitch
            checked={draft.requireConfirmation}
            label="执行前人工确认"
            onCheckedChange={(checked) => onDraftPatch('requireConfirmation', checked)}
          />
        )}
        <ToggleSwitch
          checked={draft.breakpoint}
          label="调试断点"
          onCheckedChange={(checked) => onDraftPatch('breakpoint', checked)}
        />
      </div>

      {draft.requireConfirmation && shouldUseRequireConfirmation(actionType) && (
        <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-[10px] leading-4 text-amber-800">
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.5} />
          <span>仅插件执行器生效：执行前暂停，等你在浏览器顶部横幅点「完成，继续执行」；2 分钟未确认则中止本次运行。</span>
        </div>
      )}

      {draft.continueOnError && (
        <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-[10px] leading-4 text-amber-800">
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.5} />
          <span>仅用于弹窗关闭、登录探测、可缺失元素等非关键步骤。</span>
        </div>
      )}
    </PanelSection>
  );
}
