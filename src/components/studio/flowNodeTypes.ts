import { memo } from 'react';

import { RpaStepNode, StartEndNode } from './FlowNodes';

// memo 生效的前提是 FlowCanvas 的 baseNodes / visibleNodes 分层：无运行态的节点整轮运行里 data 引用不变
export const nodeTypes = {
  rpaStep: memo(RpaStepNode),
  startEnd: memo(StartEndNode),
};
