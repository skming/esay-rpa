import { memo } from 'react';

import { RpaStepNode, StartEndNode } from './FlowNodes';

// memo 生效的前提是 FlowCanvas 保持 data 引用稳定（见 nodeDataCacheRef）
export const nodeTypes = {
  rpaStep: memo(RpaStepNode),
  startEnd: memo(StartEndNode),
};
