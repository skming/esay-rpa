import { useEffect } from 'react';

import { hasOpenOverlay, isEditableTarget, isInteractiveTarget } from '../lib/keyboardTargets';
import type { ContextMenuAction } from '../types/rpa';

export function useStudioShortcuts({
  onContextAction,
  onDeleteEdge,
  onFocusProperties,
  onSave,
  onRedo,
  onSelectNode,
  onToggleAiPanel,
  onUndo,
  selectedEdgeId,
  selectedNodeId
}: {
  onContextAction: (action: ContextMenuAction, nodeId: string) => void;
  onDeleteEdge: (edgeId: string) => void;
  onFocusProperties?: () => void;
  onSave: () => void;
  onToggleAiPanel: () => void;
  onRedo: () => void;
  onSelectNode: (nodeId: string) => void;
  onUndo: () => void;
  selectedEdgeId: string | null;
  selectedNodeId: string;
}): void {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent): void => {
      // 输入法组字期间的 keydown 属于候选框：Enter 是选词、Escape 是取消候选。
      // 中文用户每敲一个词都会经过这里，抢下来等于选不了词、也退不出候选。
      if (event.isComposing) {
        return;
      }

      // 有对话框/菜单开着就整体让路，交给它自己的键盘处理
      if (hasOpenOverlay()) {
        return;
      }

      const editable = isEditableTarget(event.target);

      if (event.key === 'Escape') {
        // 不 preventDefault：Escape 是浏览器和上层组件的退出键（退出全屏、关掉原生下拉），
        // 这里只是顺手用它退出焦点，没有理由把这些一并掐掉
        if (event.target instanceof HTMLElement) {
          event.target.blur();
        }
        // 只在焦点不在输入框时重置选中：正在属性面板改字段时按 Escape（多半是关掉某个候选层），
        // 若顺手把选中跳回 start，整个属性面板会换成 start 节点，用户没写完的编辑失去落点
        if (!editable) {
          onSelectNode('start');
        }
        return;
      }

      // 助手开关放在可编辑元素判断之前：正在助手输入框里打字时也要能一键收起面板
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'j') {
        event.preventDefault();
        onToggleAiPanel();
        return;
      }

      if (editable) {
        return;
      }

      const key = event.key.toLowerCase();
      const modifier = event.metaKey || event.ctrlKey;

      if (event.key === 'Enter' && !modifier) {
        // 焦点在按钮/链接上时 Enter 属于「激活这个控件」，不能抢
        if (isInteractiveTarget(event.target)) {
          return;
        }
        event.preventDefault();
        onFocusProperties?.();
        return;
      }

      if (modifier && key === 's') {
        event.preventDefault();
        onSave();
        return;
      }

      // ⌘⇧Z / ⌘Y 重做，⌘Z 撤销
      if (modifier && (key === 'y' || (key === 'z' && event.shiftKey))) {
        event.preventDefault();
        onRedo();
        return;
      }

      if (modifier && key === 'z') {
        event.preventDefault();
        onUndo();
        return;
      }

      if (modifier && key === 'd') {
        event.preventDefault();
        onContextAction('duplicate', selectedNodeId);
        return;
      }

      if (key === 'b') {
        event.preventDefault();
        onContextAction('breakpoint', selectedNodeId);
        return;
      }

      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault();
        if (selectedEdgeId !== null) {
          onDeleteEdge(selectedEdgeId);
        } else {
          onContextAction('delete', selectedNodeId);
        }
        return;
      }

    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onContextAction, onDeleteEdge, onRedo, onSave, onSelectNode, onToggleAiPanel, onUndo, selectedEdgeId, selectedNodeId]);
}
