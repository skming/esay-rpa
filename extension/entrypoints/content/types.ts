export interface DomElementSummary {
  ref: string;
  tag: string;
  role: string | null;
  /** 可访问性名称：aria-label/label/placeholder/alt/title/文本，按序取第一个非空 */
  name: string;
  text: string;
  visible: boolean;
  rect: { x: number; y: number; width: number; height: number };
}

export type ExtractedTableRow = Record<string, string> | string[];

// type 名与 browser_action_runner.py 的 Playwright 节点类型一致，两种执行器共用同一套协议。
export interface ContentAction {
  type:
  | 'query'
  | 'find'
  | 'browser.click'
  | 'browser.fill'
  | 'browser.extract'
  | 'browser.hover'
  | 'browser.select'
  | 'browser.press'
  | 'browser.scroll'
  | 'browser.check'
  | 'browser.drag'
  | 'browser.elementState'
  | 'browser.extractAll'
  | 'browser.ensureLogin'
  | 'scrollIntoView'
  | 'resolveRect'
  | 'highlight'
  | 'automation.pointer'
  | 'automation.activity'
  | 'automation.pageBlock'
  | 'takeover.show'
  | 'takeover.hide';
  /** 优先定位方式：上一次 query/find 快照里返回的 ref */
  ref?: string;
  /** 兼容/兜底定位方式：裸 CSS 选择器（用于手工测试或 ref 未产出的场景） */
  selector?: string;
  /** browser.drag：拖拽目标 ref（优先于 targetSelector）；browser.ensureLogin：登出态探测选择器 */
  targetRef?: string;
  /** browser.drag/browser.ensureLogin 使用，同 targetRef 注释 */
  targetSelector?: string;
  /** browser.fill/select/press 使用：分别为待填文本/待选 option value/按键名（如 Enter） */
  inputValue?: string;
  /** browser.extract 使用：对齐 Playwright 执行器的 text/count/attribute/html/table 抽取模式 */
  extractMode?: 'text' | 'count' | 'attribute' | 'html' | 'table';
  /** browser.extract extractMode=attribute 时读取的属性名，默认 href */
  attribute?: string;
  /** browser.check 使用：目标勾选状态，默认 true（对齐 BrowserActionRunner 的 checked 字段名） */
  checked?: boolean;
  /** find 使用：自然语言/关键词描述，用于在当前快照里做规则匹配打分 */
  query?: string;
  /** find 使用：返回候选数量上限，默认 10 */
  limit?: number;
  /** browser.scroll 使用：page-level 滚动像素数，对齐 BrowserActionRunner 的 distance 字段名 */
  distance?: number;
  /** highlight 使用：高亮框保留时长（毫秒），默认 900 */
  durationMs?: number;
  /** takeover.show 使用：展示给用户的接管说明文案 */
  message?: string;
  /** takeover.show 使用：所属的后端任务 id，点击"继续"按钮时带回去调 resume 接口 */
  taskId?: string;
  /** automation.pointer 使用：视口坐标，由 background 的 CDP 可信输入路径回传视觉反馈 */
  x?: number;
  y?: number;
  /** automation.pointer 使用：是否在该坐标绘制点击波纹 */
  pulse?: boolean;
  /** automation.pageBlock 使用：运行中禁用页面交互，人工接管/流程结束时放开 */
  blocked?: boolean;
}
