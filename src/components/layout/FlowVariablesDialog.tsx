import { CheckCircle2, CircleAlert, Globe, KeyRound, Plus, Settings2, Trash2, Variable, Workflow } from 'lucide-react';
import type { KeyboardEvent, ReactElement } from 'react';
import { useState } from 'react';

import type { RuntimeVariable, VariableCategory } from '../../types/rpa';
import { Button } from '../ui/button';
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';
import { Input } from '../ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { Switch } from '../ui/switch';

const VARIABLE_TYPES: RuntimeVariable['type'][] = ['String', 'Integer', 'Boolean', 'List', 'Dict'];

export function FlowVariablesDialog({
  onAdd,
  onOpenChange,
  onRemove,
  onUpdate,
  open,
  variables,
}: {
  onAdd: (category?: VariableCategory) => void;
  onOpenChange: (open: boolean) => void;
  onRemove: (name: string) => void;
  onUpdate: (name: string, patch: Partial<RuntimeVariable>) => void;
  open: boolean;
  variables: RuntimeVariable[];
}): ReactElement {
  const flowVars = variables.filter((v) => (v.category ?? 'flow') === 'flow');
  const globalVars = variables.filter((v) => v.category === 'environment' || v.category === 'credential');

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="flex max-h-[85vh] w-175 flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle>变量管理</DialogTitle>
        </DialogHeader>

        <DialogBody className="space-y-4">
          <Section
            accent="blue"
            count={flowVars.length}
            description="随当前流程版本保存，仅对本流程生效。用于传递运行参数、存储采集结果等。"
            icon={<Workflow className="h-3.5 w-3.5" strokeWidth={1.5} />}
            title="当前流程变量"
          >
            <VariableList
              emptyText="暂无流程变量，点击下方添加"
              onAdd={() => onAdd('flow')}
              onRemove={onRemove}
              onUpdate={onUpdate}
              showScope
              variables={flowVars}
            />
          </Section>

          <Section
            accent="accent"
            count={globalVars.length}
            description="跨流程共享，保存在本地应用中。适合存储 API 密钥、账号密码、服务地址等敏感配置。"
            icon={<Globe className="h-3.5 w-3.5" strokeWidth={1.5} />}
            title="全局变量"
          >
            <VariableList
              emptyText="暂无全局变量，点击下方添加"
              globalMode
              onAdd={() => onAdd('credential')}
              onRemove={onRemove}
              onUpdate={onUpdate}
              showScope={false}
              variables={globalVars}
            />
          </Section>
        </DialogBody>

        <DialogFooter>
          <Button onClick={() => onOpenChange(false)} variant="primary">完成</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Section({
  accent,
  children,
  count,
  description,
  icon,
  title,
}: {
  accent: 'blue' | 'accent';
  children: ReactElement;
  count: number;
  description: string;
  icon: ReactElement;
  title: string;
}): ReactElement {
  const colors = {
    blue: { border: 'border-blue-200', header: 'bg-blue-50', icon: 'text-blue-600', badge: 'border-blue-200 bg-blue-50 text-blue-700' },
    accent: { border: 'border-accent-line', header: 'bg-accent-soft', icon: 'text-accent-strong', badge: 'border-accent-line bg-accent-soft text-accent-strong' },
  }[accent];

  return (
    <div className={`rounded-lg border ${colors.border} overflow-hidden`}>
      <div className={`flex items-start gap-2.5 px-3.5 py-3 ${colors.header}`}>
        <span className={`mt-0.5 ${colors.icon}`}>{icon}</span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-semibold text-slate-800">{title}</span>
            <span className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${colors.badge}`}>{count} 个</span>
          </div>
          <p className="mt-0.5 text-[11px] text-slate-500">{description}</p>
        </div>
      </div>
      <div className="px-3.5 py-3">{children}</div>
    </div>
  );
}

function VariableList({
  emptyText,
  globalMode = false,
  onAdd,
  onRemove,
  onUpdate,
  showScope,
  variables,
}: {
  emptyText: string;
  globalMode?: boolean;
  onAdd: () => void;
  onRemove: (name: string) => void;
  onUpdate: (name: string, patch: Partial<RuntimeVariable>) => void;
  showScope: boolean;
  variables: RuntimeVariable[];
}): ReactElement {
  return (
    <div className="space-y-2">
      {variables.length === 0 ? (
        <div className="rounded-md border border-dashed border-slate-200 py-5 text-center">
          <Variable className="mx-auto mb-1.5 h-5 w-5 text-slate-300" strokeWidth={1.5} />
          <p className="text-[11px] text-slate-500">{emptyText}</p>
        </div>
      ) : (
        variables.map((v, i) => (
          <VariableRow
            globalMode={globalMode}
            key={i}
            onRemove={onRemove}
            onUpdate={onUpdate}
            showScope={showScope}
            variable={v}
          />
        ))
      )}
      <Button className="w-full text-[11px]" onClick={onAdd} variant="outline">
        <Plus className="h-3.5 w-3.5" strokeWidth={1.5} />
        {globalMode ? '新增全局变量' : '新增流程变量'}
      </Button>
    </div>
  );
}

const GLOBAL_CATEGORY_OPTIONS: Array<{ label: string; value: VariableCategory; icon: ReactElement }> = [
  { label: '凭据', value: 'credential', icon: <KeyRound className="h-3 w-3" strokeWidth={1.5} /> },
  { label: '环境变量', value: 'environment', icon: <Settings2 className="h-3 w-3" strokeWidth={1.5} /> },
];

function VariableRow({
  globalMode,
  onRemove,
  onUpdate,
  showScope,
  variable,
}: {
  globalMode: boolean;
  onRemove: (name: string) => void;
  onUpdate: (name: string, patch: Partial<RuntimeVariable>) => void;
  showScope: boolean;
  variable: RuntimeVariable;
}): ReactElement {
  const [draftName, setDraftName] = useState(variable.name);

  // 名称只在失焦时提交（见 commitName），故需同步外部变化，否则撤销/切换变量不会反映到输入框。
  // 放渲染期而非 effect：effect 会先用旧名字绘一帧，撤销时输入框闪一下旧值
  const [syncedName, setSyncedName] = useState(variable.name);
  if (variable.name !== syncedName) {
    setSyncedName(variable.name);
    setDraftName(variable.name);
  }

  const commitName = (): void => {
    const trimmed = draftName.trim();
    if (trimmed.length === 0) { setDraftName(variable.name); return; }
    if (trimmed !== variable.name) onUpdate(variable.name, { name: trimmed });
  };

  const handleNameKey = (e: KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === 'Enter') e.currentTarget.blur();
  };

  const categoryIcon = variable.category === 'credential'
    ? <KeyRound className="h-3 w-3 text-red-400" strokeWidth={1.5} />
    : variable.category === 'environment'
      ? <Settings2 className="h-3 w-3 text-amber-500" strokeWidth={1.5} />
      : null;

  return (
    <div className="rounded-md border border-slate-200 bg-white p-2.5">
      <div className="flex items-center gap-2">
        {categoryIcon && <span className="shrink-0">{categoryIcon}</span>}
        <Input
          className="flex-1 font-mono text-[11px]"
          onBlur={commitName}
          onChange={(e) => setDraftName(e.target.value)}
          onKeyDown={handleNameKey}
          placeholder="变量名"
          value={draftName}
        />
        <Select
          onValueChange={(v) => onUpdate(variable.name, { type: v as RuntimeVariable['type'] })}
          value={variable.type}
        >
          <SelectTrigger className="w-20.5 font-mono text-[11px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {VARIABLE_TYPES.map((t) => (
              <SelectItem key={t} value={t}>{t}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        {globalMode && (
          <Select
            onValueChange={(v) => onUpdate(variable.name, { category: v as VariableCategory })}
            value={variable.category ?? 'credential'}
          >
            <SelectTrigger className="w-22.5 text-[11px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {GLOBAL_CATEGORY_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  <span className="flex items-center gap-1.5">{opt.icon}{opt.label}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Button
          aria-label="删除变量"
          className="h-8 w-9 shrink-0 px-0"
          onClick={() => onRemove(variable.name)}
          variant="outline"
        >
          <Trash2 className="h-3.5 w-3.5" strokeWidth={1.5} />
        </Button>
      </div>

      <div className="mt-2 flex items-center gap-2">
        <Input
          className="flex-1 font-mono text-[11px]"
          onChange={(e) => onUpdate(variable.name, { value: e.target.value })}
          placeholder="默认值（可选）"
          type={variable.sensitive ? 'password' : 'text'}
          value={variable.value}
        />
        {showScope && (
          <Select
            onValueChange={(v) => onUpdate(variable.name, { scope: v as RuntimeVariable['scope'] })}
            value={variable.scope}
          >
            <SelectTrigger className="w-17 text-[11px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(['全局', '循环', '局部'] as const).map((s) => (
                <SelectItem key={s} value={s}>{s}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <label className="flex cursor-pointer select-none items-center gap-1.5 text-[11px] text-slate-500">
          <Switch
            aria-label={`${variable.name} 敏感变量`}
            checked={variable.sensitive === true}
            onCheckedChange={(c) => onUpdate(variable.name, { sensitive: c })}
          />
          <span>敏感</span>
        </label>
      </div>
      <VariableValuePreview variable={variable} />
    </div>
  );
}

function VariableValuePreview({ variable }: { variable: RuntimeVariable }): ReactElement {
  const parsed = parseVariableValue(variable.value, variable.type);
  const ok = parsed.ok;

  return (
    <div className={`mt-2 rounded-md border px-2 py-1.5 text-[10px] leading-4 ${ok ? 'border-slate-200 bg-slate-50 text-slate-500' : 'border-red-200 bg-red-50 text-red-700'}`}>
      <div className="flex items-center gap-1.5">
        {ok ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.5} /> : <CircleAlert className="h-3.5 w-3.5 text-red-500" strokeWidth={1.5} />}
        <span className="font-medium">{ok ? '解析成功' : '解析失败'}</span>
        <span className="font-mono text-slate-500">{variable.type}</span>
      </div>
      <div className="mt-1 min-w-0 break-words font-mono text-[10px]">
        {variable.sensitive === true && ok ? '敏感变量已隐藏解析值' : parsed.message}
      </div>
    </div>
  );
}

function parseVariableValue(value: string, type: RuntimeVariable['type']): { ok: boolean; message: string } {
  if (type === 'String') {
    return { ok: true, message: value === '' ? '空字符串' : JSON.stringify(value) };
  }
  if (type === 'Integer') {
    const normalized = value.trim();
    if (!/^-?\d+$/.test(normalized)) {
      return { ok: false, message: '请输入整数，例如 0、42、-1' };
    }
    return { ok: true, message: String(Number.parseInt(normalized, 10)) };
  }
  if (type === 'Boolean') {
    const normalized = value.trim().toLowerCase();
    if (['true', 'false', '1', '0', 'yes', 'no', '是', '否'].includes(normalized)) {
      return { ok: true, message: ['true', '1', 'yes', '是'].includes(normalized) ? 'true' : 'false' };
    }
    return { ok: false, message: '请输入 true/false、1/0、是/否' };
  }
  if (type === 'List' || type === 'Dict') {
    try {
      const parsed = JSON.parse(value);
      if (type === 'List' && !Array.isArray(parsed)) {
        return { ok: false, message: 'List 类型必须是 JSON 数组，例如 ["a","b"]' };
      }
      if (type === 'Dict' && (parsed === null || Array.isArray(parsed) || typeof parsed !== 'object')) {
        return { ok: false, message: 'Dict 类型必须是 JSON 对象，例如 {"key":"value"}' };
      }
      return { ok: true, message: JSON.stringify(parsed, null, 2) };
    } catch {
      return { ok: false, message: type === 'List' ? '请输入合法 JSON 数组' : '请输入合法 JSON 对象' };
    }
  }
  return { ok: true, message: value };
}
