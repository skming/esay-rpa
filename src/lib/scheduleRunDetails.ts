import type { RunDetail, TaskSnapshot } from '../types/electron';

export type ScheduleRunEntry =
  | { taskId: string; state: 'ready'; run: TaskSnapshot }
  | { taskId: string; state: 'missing' | 'error'; message: string };

export async function loadScheduleRunDetails(
  taskIds: string[],
  loadDetail: (taskId: string) => Promise<RunDetail>,
): Promise<ScheduleRunEntry[]> {
  return Promise.all(taskIds.map(async (taskId): Promise<ScheduleRunEntry> => {
    try {
      const { run } = await loadDetail(taskId);
      return { taskId, state: 'ready', run };
    } catch (error) {
      if (error instanceof Error && error.message === 'Task not found') {
        return { taskId, state: 'missing', message: '运行记录不存在，可能已被清理' };
      }
      return { taskId, state: 'error', message: error instanceof Error && error.message.trim() ? error.message : '读取运行记录失败，请重试' };
    }
  }));
}
