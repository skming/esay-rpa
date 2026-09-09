// page_probe.js 是纯 JS（Playwright 侧要把它当表达式读，不能有 TS 语法），
// 类型声明单独放这里，扩展侧 import 才有类型。
export declare const PAGE_PROBE: (args: {
  scope?: string | null;
  version?: number;
}) => Record<string, unknown>;
