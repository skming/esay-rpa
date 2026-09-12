import { Code2, Download } from 'lucide-react';
import type { ReactElement } from 'react';

import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import { Button } from '../../ui/button';
import { CodeBlock } from '../../ui/CodeBlock';
import { PanelSection } from './PanelSection';

export function ScriptGeneratorSection({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const script = electron.generatedScript;
  return (
    <PanelSection title="Scrapling 脚本">
      <Button className="h-8 w-full" onClick={() => void electron.generateScraplingScript()} variant="outline">
        <Code2 className="h-3.5 w-3.5" strokeWidth={1.5} />
        生成 Python 脚本
      </Button>
      {script !== null && (
        <>
          <Button
            className="h-8 w-full"
            onClick={() => void electron.exportScraplingScript(script.content, script.filename)}
            variant="outline"
          >
            <Download className="h-3.5 w-3.5" strokeWidth={1.5} />
            导出 .py 文件
          </Button>
          <CodeBlock
            code={script.content}
            filename={script.filename}
            language="python"
            maxHeight={180}
            note={script.dependencies[0]}
            variant="dark"
          />
        </>
      )}
    </PanelSection>
  );
}
