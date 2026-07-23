import type { ReactElement } from 'react';

import type { FlowCardState } from '../../lib/taskCenter';
import { StateTag } from './surfaces';
import type { StatusTone } from './surfaces';

const META: Record<FlowCardState, { state: StatusTone; label: string }> = {
  disabled:  { state: 'idle',    label: '已禁用' },
  draft:     { state: 'idle',    label: '草稿' },
  failed:    { state: 'error',   label: '上次失败' },
  paused:    { state: 'warning', label: '已暂停' },
  published: { state: 'success', label: '已发布' },
  running:   { state: 'live',    label: '运行中' },
  scheduled: { state: 'warning', label: '已调度' },
};

export function FlowStatusBadge({ state }: { state: FlowCardState }): ReactElement {
  const meta = META[state];
  return <StateTag state={meta.state} label={meta.label} />;
}
