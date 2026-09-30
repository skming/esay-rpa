import { describe, expect, it } from 'vitest';

import {
  clampAiAssistantPosition,
  parseAiAssistantPosition,
  snapAiAssistantPosition,
} from './aiAssistantPosition';

describe('AI 助手悬浮位置', () => {
  it('把拖拽结果限制在可见窗口内', () => {
    expect(clampAiAssistantPosition({ x: -100, y: 900 }, { width: 1000, height: 800 })).toEqual({
      x: 8,
      y: 752,
    });
  });

  it('忽略损坏或不完整的持久化坐标', () => {
    expect(parseAiAssistantPosition('{bad json')).toBeNull();
    expect(parseAiAssistantPosition('{"x":20}')).toBeNull();
    expect(parseAiAssistantPosition('{"x":20,"y":30}')).toEqual({ x: 20, y: 30 });
  });

  it('释放时吸附到最近的水平边缘并保留纵向位置', () => {
    expect(snapAiAssistantPosition({ x: 420, y: 260 }, { width: 1000, height: 800 })).toEqual({
      x: 8,
      y: 260,
    });
    expect(snapAiAssistantPosition({ x: 700, y: 260 }, { width: 1000, height: 800 })).toEqual({
      x: 952,
      y: 260,
    });
  });
});
