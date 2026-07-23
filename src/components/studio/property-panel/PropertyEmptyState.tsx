import { MousePointer2 } from 'lucide-react';
import type { ReactElement } from 'react';

export function PropertyEmptyState(): ReactElement {
  return (
    <div className="grid h-full place-items-center text-center">
      <div>
        <MousePointer2 className="mx-auto mb-3 h-6 w-6 text-slate-300" strokeWidth={1.5} />
        <div className="text-[11px] font-semibold text-slate-600">点击画布中的节点</div>
        <div className="mt-1 text-[11px] text-slate-500">查看和编辑其配置属性</div>
      </div>
    </div>
  );
}
