import { describe, expect, it } from 'vitest';

import { cn } from './utils';

// tailwind-merge 的去重是按「同一属性组」做的，变体前缀不同就算不同组。Input 的校验态依赖这一点：
// aria-invalid:border-red-200 必须和无前缀的 border-slate-200 共存，只在 aria-invalid="true" 时压过它。
// 如果哪次升级把变体前缀也并进同一组，红框会静默消失——类名被丢掉不报错、类型检查也看不出来。
describe('cn 保留变体前缀类', () => {
  const INVALID = [
    'aria-invalid:border-red-200',
    'aria-invalid:bg-red-50',
    'aria-invalid:text-red-700',
    'aria-invalid:focus-visible:border-red-300',
    'aria-invalid:focus-visible:ring-red-100'
  ];

  it('与同属性的无前缀类和 focus-visible 类共存', () => {
    const result = cn(
      'border border-slate-200 bg-white text-slate-700 focus-visible:border-accent-line focus-visible:ring-accent-soft',
      INVALID.join(' '),
      'pr-8 font-mono'
    ).split(' ');

    for (const className of INVALID) {
      expect(result).toContain(className);
    }
  });

  it('调用方 className 不会顶掉校验态', () => {
    const result = cn(INVALID.join(' '), 'border-amber-200 bg-amber-50').split(' ');

    for (const className of INVALID) {
      expect(result).toContain(className);
    }
  });
});
