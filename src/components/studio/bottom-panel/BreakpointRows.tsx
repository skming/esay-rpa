import { Bug, CornerDownRight, Navigation, Play, Square, StepForward, Trash2 } from 'lucide-react';
import type { Node } from '@xyflow/react';
import type { ReactElement } from 'react';

import type { DebugControlCommand } from '../../../hooks/electronBridgeTypes';
import type { RpaNodeData, RuntimeStatus } from '../../../types/rpa';
import { IconButton } from '../../ui/button';
import { Switch } from '../../ui/switch';
import { Table, TableBody, TableCell, TableRow } from '../../ui/table';
import { PanelEmptyState } from './PanelEmptyState';

export function BreakpointRows({
  nodes,
  onDebugControl,
  onBreakpointChange,
  onJumpToNode,
  onStopDebug,
  runtimeStatus
}: {
  nodes: Node<RpaNodeData>[];
  onBreakpointChange: (nodeId: string, enabled: boolean) => void;
  onDebugControl: (command: DebugControlCommand) => void;
  onJumpToNode: (nodeId: string) => void;
  onStopDebug: () => void;
  runtimeStatus: RuntimeStatus;
}): ReactElement {
  const rows = nodes.filter((node) => node.data.breakpoint);
  const debugging = runtimeStatus === 'running';

  if (rows.length === 0) {
    return <PanelEmptyState icon={Bug} text="右键节点可添加断点 · 当前无断点" />;
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 mx-2">
        <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-600">
          <Bug className="h-3.5 w-3.5 text-red-500" strokeWidth={1.5} />
          {rows.length} 个断点
          <span className="font-normal text-slate-400">· {debugging ? '调试运行中' : '等待调试运行'}</span>
        </div>
        <div className="flex items-center gap-1">
          <IconButton disabled={!debugging} label={debugging ? '继续执行' : '调试运行中可继续执行'} onClick={() => onDebugControl('continue')}>
            <Play className="h-3.5 w-3.5" strokeWidth={1.5} />
          </IconButton>
          <IconButton disabled={!debugging} label={debugging ? '单步越过' : '调试运行中可单步越过'} onClick={() => onDebugControl('step-over')}>
            <StepForward className="h-3.5 w-3.5" strokeWidth={1.5} />
          </IconButton>
          <IconButton disabled={!debugging} label={debugging ? '单步进入' : '调试运行中可单步进入'} onClick={() => onDebugControl('step-into')}>
            <CornerDownRight className="h-3.5 w-3.5" strokeWidth={1.5} />
          </IconButton>
          <IconButton disabled={!debugging} label={debugging ? '停止调试' : '调试运行中可停止调试'} onClick={onStopDebug}>
            <Square className="h-3.5 w-3.5" strokeWidth={1.5} />
          </IconButton>
        </div>
      </div>

      <Table className="table-fixed">
        <TableBody>
          {rows.map((node, index) => (
            <TableRow key={node.id}>
              <TableCell className="pl-2">
                <div className="truncate text-[12px] font-semibold text-slate-700">{node.data.title}</div>
                <div className="truncate font-mono text-[10px] text-slate-400">
                  Step {index + 1} · {node.id}
                </div>
              </TableCell>
              <TableCell className="w-11">
                <IconButton className="h-7 w-7" label="跳转节点" onClick={() => onJumpToNode(node.id)}>
                  <Navigation className="h-3.5 w-3.5" strokeWidth={1.5} />
                </IconButton>
              </TableCell>
              <TableCell className="w-17">
                <Switch checked aria-label={`${node.data.title} 断点启用状态`} onCheckedChange={(checked) => onBreakpointChange(node.id, checked)} />
              </TableCell>
              <TableCell className="w-11 pr-2">
                <IconButton label="删除断点" onClick={() => onBreakpointChange(node.id, false)}>
                  <Trash2 className="h-3.5 w-3.5 text-red-500" strokeWidth={1.5} />
                </IconButton>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
