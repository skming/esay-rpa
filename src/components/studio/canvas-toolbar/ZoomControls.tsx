import { ZoomIn, ZoomOut } from 'lucide-react';
import type { ReactElement } from 'react';

import { Button, IconButton } from '../../ui/button';

export function ZoomControls({
  onFitView,
  onZoomIn,
  onZoomOut,
  zoom
}: {
  onFitView: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  zoom: number;
}): ReactElement {
  return (
    <div className="flex shrink-0 overflow-hidden rounded-md border border-slate-200">
      <IconButton className="rounded-none border-0" label="缩小" onClick={onZoomOut}>
        <ZoomOut className="h-3.5 w-3.5" strokeWidth={1.5} />
      </IconButton>
      {/* 中间按钮显示当前缩放百分比，点击触发适应视图（非重置为100%） */}
      <Button className="h-7 min-w-12 rounded-none border-x border-slate-200 px-2 font-mono text-[11px] text-slate-600 hover:bg-slate-50" onClick={onFitView} variant="ghost">
        {Math.round(zoom * 100)}%
      </Button>
      <IconButton className="rounded-none border-0" label="放大" onClick={onZoomIn}>
        <ZoomIn className="h-3.5 w-3.5" strokeWidth={1.5} />
      </IconButton>
    </div>
  );
}
