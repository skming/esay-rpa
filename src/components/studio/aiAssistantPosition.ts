export type AiAssistantPosition = { x: number; y: number };

export const AI_ASSISTANT_SIZE = 40;
const EDGE_GAP = 8;

export function clampAiAssistantPosition(
  position: AiAssistantPosition,
  viewport: { width: number; height: number },
): AiAssistantPosition {
  const maxX = Math.max(EDGE_GAP, viewport.width - AI_ASSISTANT_SIZE - EDGE_GAP);
  const maxY = Math.max(EDGE_GAP, viewport.height - AI_ASSISTANT_SIZE - EDGE_GAP);
  return {
    x: Math.min(maxX, Math.max(EDGE_GAP, position.x)),
    y: Math.min(maxY, Math.max(EDGE_GAP, position.y)),
  };
}

export function snapAiAssistantPosition(
  position: AiAssistantPosition,
  viewport: { width: number; height: number },
): AiAssistantPosition {
  const clamped = clampAiAssistantPosition(position, viewport);
  const right = Math.max(EDGE_GAP, viewport.width - AI_ASSISTANT_SIZE - EDGE_GAP);
  return {
    x: clamped.x - EDGE_GAP <= right - clamped.x ? EDGE_GAP : right,
    y: clamped.y,
  };
}

export function parseAiAssistantPosition(value: string | null): AiAssistantPosition | null {
  if (value === null) return null;
  try {
    const parsed = JSON.parse(value) as Partial<AiAssistantPosition>;
    if (Number.isFinite(parsed.x) && Number.isFinite(parsed.y)) {
      return { x: parsed.x as number, y: parsed.y as number };
    }
  } catch {
    return null;
  }
  return null;
}
