import { AlertCircle, Ban, CheckCircle2, Loader2 } from 'lucide-react';
import type { Node } from '@xyflow/react';
import type { ReactElement } from 'react';

import { kindStyles } from '../../../data/studioData';
import type { RpaNodeData } from '../../../types/rpa';

const ACTION_TYPE_LABELS: Record<string, string> = {
  // browser
  'browser.open': '打开网页',
  'browser.fill': '输入文本',
  'browser.click': '点击元素',
  'browser.wait': '等待元素',
  'browser.screenshot': '页面截图',
  'browser.scroll': '滚动页面',
  'browser.tab.switch': '切换标签页',
  'browser.tab.open': '打开标签页',
  'browser.tab.close': '关闭标签页',
  'browser.extract': '获取文本',
  'browser.scrape': '提取数据',
  'browser.select': '下拉选择',
  'browser.check': '复选框',
  'browser.drag': '拖拽操作',
  'browser.fetch': '动态抓取',
  // ui
  'ui.click': '点击控件',
  'ui.fill': '输入文字',
  'ui.extract': '获取属性',
  'ui.wait': '等待控件',
  'ui.screenshot': '截图控件',
  'ui.select': '下拉选择',
  'ui.check': '复选框',
  'ui.drag': '拖拽操作',
  'ui.list': '列表操作',
  // excel
  'excel.open': '打开工作簿',
  'excel.read': '读取单元格',
  'excel.write': '写入单元格',
  'excel.addrow': '新增数据行',
  'excel.deleterow': '删除数据行',
  'excel.save': '保存文件',
  'excel.getrowcount': '获取行数',
  'excel.filter': '筛选/排序',
  'excel.export': '导出 CSV',
  // file
  'file.read': '读取文件',
  'file.write': '写入文件',
  'file.copy': '复制/移动',
  'file.delete': '删除文件',
  'file.list': '遍历文件夹',
  'file.compress': '压缩解压',
  'file.rename': '重命名',
  'file.watch': '监听变化',
  // control
  'control.condition': '条件判断',
  'control.foreach': '循环列表',
  'control.delay': '等待延迟',
  'control.break': '终止循环',
  'control.retry': '失败重试',
  'control.try': '异常处理',
  'control.subprocess': '运行子流程',
  'control.noop': '空节点',
  // variable
  'variable.set': '设置变量',
  'variable.get': '读取变量',
  'variable.input': '用户输入',
  'variable.log': '输出日志',
  'variable.notify': '发送通知',
  'variable.clipboard': '剪贴板',
  'variable.compare': '比较变量',
  // script
  'http.request': 'HTTP 请求',
  'script.javascript': 'JavaScript',
  'script.python': 'Python 脚本',
  'script.shell': 'Shell 命令',
  'script.websocket': 'WebSocket',
  // data
  'data.json.parse': 'JSON 解析',
  'data.string.transform': '字符串处理',
  'data.convert': '格式转换',
  'data.encrypt': '加解密',
  'data.regex.match': '正则匹配',
  'data.list.map': '列表映射',
  'data.math.compute': '数学计算',
};

export function NodeSummary({ node }: { node: Node<RpaNodeData> }): ReactElement {
  const style = kindStyles[node.data.kind];
  const Icon = style.icon;
  const actionType = node.data.action?.type;
  const componentLabel = (actionType && ACTION_TYPE_LABELS[actionType]) ?? style.label;

  return (
    <div className="mb-3 flex items-center gap-2.5 rounded-lg border p-2.5" style={{ background: style.bg, borderColor: `${style.accent}33` }}>
      <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-white shadow-xs" style={{ color: style.accent }}>
        <Icon className="h-4 w-4" strokeWidth={1.5} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[11px] font-bold text-slate-800">{node.data.title}</div>
        <div className="mt-0.5 text-[11px]" style={{ color: style.text }}>
          {componentLabel}
        </div>
      </div>
      {node.data.status === 'running' && <Loader2 className="h-4 w-4 animate-spin text-blue-400" strokeWidth={1.5} />}
      {node.data.status === 'done' && <CheckCircle2 className="h-4 w-4 text-emerald-500" strokeWidth={1.5} />}
      {node.data.status === 'error' && <AlertCircle className="h-4 w-4 text-red-500" strokeWidth={1.5} />}
      {node.data.status === 'skipped' && <Ban className="h-4 w-4 text-slate-300" strokeWidth={1.5} />}
    </div>
  );
}
