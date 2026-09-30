export const MIN_CANVAS_WIDTH = 560;
export const AI_SIDEBAR_WIDTH = 340;

export function canDockAiSidebar(canvasWidth: number, sidebarAlreadyDocked: boolean): boolean {
  const widthWithSidebar = sidebarAlreadyDocked
    ? canvasWidth
    : canvasWidth - AI_SIDEBAR_WIDTH;
  return widthWithSidebar >= MIN_CANVAS_WIDTH;
}
