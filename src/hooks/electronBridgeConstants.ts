import type { RuntimeProgress } from '../types/rpa';

export const initialProgress: RuntimeProgress = {
  currentStep: 0,
  totalSteps: 0,
  percent: 0,
  elapsedMs: 0
};
