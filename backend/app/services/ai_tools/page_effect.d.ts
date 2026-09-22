// page_effect.js 是纯 JS（Playwright 侧要把它当表达式读，不能有 TS 语法），
// 类型声明单独放这里，扩展侧 import 才有类型。
export declare const EFFECT_SIGNATURE: () => Record<string, unknown>;
export declare const TARGET_STATE: (el: Element | null) => Record<string, unknown>;
export declare const SETTLE_AFTER_ACTION: (args: {
  action?: string;
  ref?: string | null;
}) => Promise<Record<string, unknown>>;
