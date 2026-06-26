import type { ReactElement } from 'react';

import type { RuntimeVariable } from '../../../types/rpa';
import { Badge } from '../../ui/badge';
import { getTypeVariant } from './bottomPanelUtils';

export function TypeBadge({ type }: { type: RuntimeVariable['type'] }): ReactElement {
  return (
    <Badge className="w-fit font-mono" variant={getTypeVariant(type)}>
      {type}
    </Badge>
  );
}
