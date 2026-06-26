import { Code2 } from 'lucide-react';
import type { ReactElement } from 'react';

import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import { Button } from '../../ui/button';
import { CodeBlock } from '../../ui/CodeBlock';
import { PanelSection } from './PanelSection';

export function ScriptGeneratorSection({ electron }: { electron: ElectronBridgeState }): ReactElement {
  return (
    <PanelSection title="Scrapling 脚本">
      <Button className="h-8 w-full" onClick={() => void electron.generateScraplingScript()} variant="outline">
        <Code2 className="h-3.5 w-3.5" strokeWidth={1.5} />
        生成 Python 脚本
      </Button>
      {electron.generatedScript !== null && (
        <CodeBlock
          code={electron.generatedScript.content}
          filename={electron.generatedScript.filename}
          language="python"
          maxHeight={180}
          note={electron.generatedScript.dependencies[0]}
          variant="dark"
        />
      )}
    </PanelSection>
  );
}
