import { CheckCircle2, Plus, TriangleAlert } from 'lucide-react';
import type { Node } from '@xyflow/react';
import type { ReactElement } from 'react';
import { useMemo } from 'react';

import { buildNodeVariableDiagnostics } from '../../../lib/nodeVariableDiagnostics';
import { mergeRuntimeVariables } from '../../../lib/runtimeVariables';
import { validateNodeConfigurationInFlow, type RunValidationIssue } from '../../../lib/runValidation';
import { useFlowVariableStore } from '../../../stores/useFlowVariableStore';
import type { RpaNodeData, RuntimeVariable } from '../../../types/rpa';
import { Badge } from '../../ui/badge';

export function NodeValidationSummary({
  inputVariables,
  node,
  runtimeVariables,
  flowEdges,
  flowNodes
}: {
  inputVariables: RuntimeVariable[];
  node: Node<RpaNodeData>;
  runtimeVariables: RuntimeVariable[];
  flowEdges: import('@xyflow/react').Edge[];
  flowNodes: Node<RpaNodeData>[];
}): ReactElement | null {
  const addNamedInputVariable = useFlowVariableStore((s) => s.addNamedInputVariable);
  const storedInputVariables = useFlowVariableStore((s) => s.inputVariables);
  const existingNames = useMemo(() => new Set(storedInputVariables.map((v) => v.name)), [storedInputVariables]);

  const availableVariableNames = mergeRuntimeVariables(inputVariables, runtimeVariables).map((v) => v.name);
  const variableDiagnostics = buildNodeVariableDiagnostics(node, availableVariableNames, flowNodes);

  const missingVarNames = [
    ...new Set(
      variableDiagnostics.inputIssues
        .filter((i) => i.message.includes('当前不存在'))
        .map((i) => i.variableName)
    )
  ];

  // 已在上方缺失变量区块单独展示过，此处滤掉避免同一问题出现两次
  const missingVarMessageSet = new Set(missingVarNames.map((n) => `变量"${n}"当前不存在`));
  const structureIssues = validateNodeConfigurationInFlow(node, flowNodes, flowEdges, availableVariableNames)
    .filter((i) => !missingVarMessageSet.has(i.message));
  const outputIssues = variableDiagnostics.outputIssues.map<RunValidationIssue>((i) => ({
    nodeId: node.id,
    severity: i.severity,
    message: i.message
  }));

  const summaryIssues = dedupeIssues([...structureIssues, ...outputIssues]);
  const visibleSummaryIssues = summaryIssues.slice(0, 4);
  const remainingSummaryIssues = summaryIssues.slice(4);
  const hasMissing = missingVarNames.length > 0;
  const errorCount = summaryIssues.filter((i) => i.severity === 'error').length + (hasMissing ? missingVarNames.length : 0);
  const warnCount = summaryIssues.filter((i) => i.severity === 'warn').length;
  const hasAnyIssue = hasMissing || summaryIssues.length > 0;

  if (!hasAnyIssue) {
    return (
      <div className="mb-3 flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 py-2 text-[11px] text-emerald-700">
        <CheckCircle2 className="h-3.5 w-3.5 shrink-0" strokeWidth={1.5} />
        <span className="min-w-0 flex-1">当前节点配置完整，可参与运行。</span>
        <Badge variant="emerald">就绪</Badge>
      </div>
    );
  }

  return (
    <div className="mb-3 space-y-1.5">
      {hasMissing && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-2.5 py-2">
          <div className="flex items-center gap-2 text-[11px] font-medium text-red-800">
            <TriangleAlert className="h-3.5 w-3.5 shrink-0" strokeWidth={1.5} />
            <span className="flex-1">引用了未定义的流程变量</span>
            <Badge variant="amber">{missingVarNames.length} 个未定义</Badge>
          </div>
          <div className="mt-2 space-y-1">
            {missingVarNames.map((name) => (
              <div
                className="flex items-center justify-between rounded border border-red-100 bg-white px-2 py-1.5 text-[11px]"
                key={name}
              >
                <span className="font-mono text-red-700">{name}</span>
                {existingNames.has(name) ? (
                  <span className="text-[10px] text-emerald-600">已在变量列表中</span>
                ) : (
                  <button
                    className="flex items-center gap-1 rounded bg-accent px-2 py-0.5 text-[10px] font-medium text-white transition hover:bg-accent-press"
                    onClick={() => addNamedInputVariable(name)}
                    title={`添加 ${name} 到流程变量`}
                    type="button"
                  >
                    <Plus className="h-2.5 w-2.5" strokeWidth={2} />
                    添加到变量
                  </button>
                )}
              </div>
            ))}
          </div>
          <p className="mt-1.5 text-[10px] text-red-600">
            添加后请在「输入变量」面板设置默认值，或由上游节点输出该变量。
          </p>
        </div>
      )}

      {summaryIssues.length > 0 && (
        <div className={`rounded-lg border px-2.5 py-2 ${errorCount > 0 ? 'border-amber-200 bg-amber-50 text-amber-900' : 'border-rule bg-surface text-ink'}`}>
          <div className="flex items-center gap-2 text-[11px] font-medium">
            <TriangleAlert className="h-3.5 w-3.5 shrink-0" strokeWidth={1.5} />
            <span className="min-w-0 flex-1">
              {errorCount > 0 ? '运行前需要修复以下问题' : '当前节点存在运行提醒'}
            </span>
            {errorCount > 0 && <Badge variant="amber">{errorCount} 个错误</Badge>}
            {warnCount > 0 && <Badge variant="default">{warnCount} 个提醒</Badge>}
          </div>
          <div className="mt-2 space-y-1.5">
            {visibleSummaryIssues.map((issue) => <ValidationIssueRow issue={issue} key={issueKey(issue)} />)}
            {remainingSummaryIssues.length > 0 && (
              <details className="group">
                <summary className="cursor-pointer rounded-md border border-black/5 bg-white/70 px-2 py-1.5 text-[10px] font-medium transition hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40">
                  还有 {remainingSummaryIssues.length} 项，展开查看
                </summary>
                <div className="mt-1.5 space-y-1.5">
                  {remainingSummaryIssues.map((issue) => <ValidationIssueRow issue={issue} key={issueKey(issue)} />)}
                </div>
              </details>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ValidationIssueRow({ issue }: { issue: RunValidationIssue }): ReactElement {
  return (
    <div className="rounded-md border border-black/5 bg-white/70 px-2 py-1.5 text-[10px] leading-4">
      {issue.message}
    </div>
  );
}

function issueKey(issue: RunValidationIssue): string {
  return `${issue.nodeId}-${issue.severity}-${issue.message}`;
}

function dedupeIssues(issues: RunValidationIssue[]): RunValidationIssue[] {
  const seen = new Set<string>();
  return issues.filter((issue) => {
    const key = `${issue.nodeId}:${issue.severity}:${issue.message}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
