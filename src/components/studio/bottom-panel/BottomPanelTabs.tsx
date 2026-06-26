import { Download, X } from 'lucide-react';
import type { ReactElement } from 'react';

import type { BottomTab } from '../../../types/rpa';
import { IconButton } from '../../ui/button';
import { RefreshIconButton } from '../../ui/refresh-button';
import { Tabs, TabsList, TabsTrigger } from '../../ui/tabs';

export function BottomPanelTabs({
  activeTab,
  artifactCount,
  errorCount,
  onActiveTabChange,
  onClose,
  onExportLogs,
  onRefresh
}: {
  activeTab: BottomTab;
  artifactCount: number;
  errorCount: number;
  onActiveTabChange: (tab: BottomTab) => void;
  onClose: () => void;
  onExportLogs: () => void;
  onRefresh?: () => void;
}): ReactElement {
  return (
    <div className="flex h-9 items-center justify-between border-b border-slate-100 px-3">
      <Tabs onValueChange={(value) => onActiveTabChange(value as BottomTab)} value={activeTab}>
        <TabsList className="flex h-9 gap-5 text-xs">
          <TabsTrigger className="h-full" value="logs">
            运行日志
          </TabsTrigger>
          <TabsTrigger className="h-full" value="variables">
            变量监控
          </TabsTrigger>
          <TabsTrigger className="h-full" value="breakpoints">
            断点调试
          </TabsTrigger>
          <TabsTrigger className="h-full" value="errors">
            错误 ({errorCount})
          </TabsTrigger>
          <TabsTrigger className="h-full" value="artifacts">
            采集结果 ({artifactCount})
          </TabsTrigger>
        </TabsList>
      </Tabs>
      <div className="flex items-center gap-1">
        {onRefresh !== undefined && <RefreshIconButton label="刷新" onClick={onRefresh} />}
        <IconButton label="导出日志" onClick={onExportLogs}>
          <Download className="h-3.5 w-3.5" strokeWidth={1.5} />
        </IconButton>
        <IconButton label="关闭面板" onClick={onClose}>
          <X className="h-3.5 w-3.5" strokeWidth={1.5} />
        </IconButton>
      </div>
    </div>
  );
}
