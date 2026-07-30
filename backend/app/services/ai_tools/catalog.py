"""节点类型目录：供 list_node_types 工具告知模型有哪些节点可用。"""
from __future__ import annotations

from app.services.ai_tools.script_capabilities import describe_script_capabilities


# type 字段为点分字符串，所有配置字段平铺在节点根层（不嵌套在 config 对象中）。
# output_var_field: 该节点用于定义输出变量的字段名（填入变量名后该变量在后续节点可用）

NODE_TYPE_CATALOG: list[dict[str, str]] = [
    # 浏览器操作 (kind: browser)
    {
        "type": "browser.open",
        "key_fields": "targetUrl",
        "output_var_field": "（无输出变量）",
        "description": (
            "打开/跳转网页，需 targetUrl 字段（兼容历史 url 字段，但新流程必须使用 targetUrl）。"
            "可选字段：delayMs（导航完成后额外等待，SPA hash 路由建议 2000-3000）；"
            "clearStorage: true（导航后清除该域名的 localStorage/sessionStorage，然后自动 reload——"
            "**仅用于诊断或用户明确要求重置登录态**：默认不要设置。"
            "带登录态检测的流程应保留 Cookies/localStorage 复用已登录会话；"
            "只有 inspect_page/运行日志证明过期 token 导致 SPA 卡死时，才临时启用）；"
            "clearCookies: true（清除该浏览器上下文的所有 Cookie，与 clearStorage 可同时使用）"
        ),
    },
    {
        "type": "browser.ensureLogin",
        "key_fields": "targetUrl, selector（已登录特征）, targetSelector（未登录特征）",
        "output_var_field": "firstValueVariable（探测结果 logged_in|login_required 存入该变量）",
        "description": (
            "登录态优先探测：打开 targetUrl 并判断持久会话是否仍有效，输出 logged_in 或 login_required。"
            "**含登录的流程应以此节点开头**，后接 control.condition（表达式写裸变量名，如 login_status == 'login_required'）"
            "只在需要登录时才走登录链路——Cookie 有效期内可完全跳过账号密码/滑块验证。"
            "selector 填已登录才出现的元素（如用户头像/工作台导航），targetSelector 填未登录特征（如 input[type='password']）；"
            "两者都不填时按「URL 含 login/密码框存在」启发式判断。"
        ),
    },
    {
        "type": "browser.click",
        "key_fields": "selector",
        "output_var_field": "（无输出变量）",
        "description": (
            "点击页面元素，需 selector。"
            "可选字段：continueOnError: true（点击可选元素时必须加，防止元素不存在时流程中断）；"
            "force: true（绕过 Playwright 可见性检查直接点击——用于元素存在但 CSS 隐藏、"
            "visibility:hidden/opacity:0 导致 selector_match_not_visible 且该操作必须执行的场景；"
            "不确定是否应该跳过时，先询问用户）；"
            "fallbackSelectors（换行分隔的备选 selector，运行时主 selector 未命中会自动逐个尝试）；"
            "anchorText（元素的可见文字锚点，如按钮文案——运行时兜底按文字定位，抗页面改版）。"
            "selector 支持语义引擎（text=\"文案\" / role=button[name=\"文案\"]）和 iframe 穿透语法"
            "（iframe选择器 >>> 内部选择器，可多层）；open Shadow DOM 由 CSS 自动穿透无需特殊写法。"
        ),
    },
    {
        "type": "browser.fill",
        "key_fields": "selector, inputValue",
        "output_var_field": "（无输出变量）",
        "description": (
            "向输入框填写文本，inputValue 可用 ${var.xxx} 引用变量。"
            "可选：fallbackSelectors / anchorText（同 browser.click，运行时 selector 自愈兜底）。"
        ),
    },
    {
        "type": "browser.extract",
        "key_fields": "selector, extractMode",
        "output_var_field": "outputVariable（必填，提取结果存入该变量）",
        "description": (
            "提取元素到变量，extractMode: text|html|attribute|count|table。"
            "table 模式（selector 指向 tbody tr 数据行）会自动识别表头并把每行存为 {列名:值} 对象、"
            "自动剔除框架影子残行，可直接 file.write/excel 导出干净结构化数据，无需额外的表头节点或清洗脚本。"
            "可选 outputSchema（JSON 数组）：声明期望输出字段，如 "
            '[{"name":"品名","aliases":["名称","商品"]},{"name":"价格"},{"name":"备注","required":false}]'
            "——运行时按 精确→别名→包含 匹配表头并把行改名对齐成 schema 字段（无表头表格按列序命名）；"
            "必需字段未命中会直接报错并列出实际可用列。**用户明确说了要哪些字段时必须声明 outputSchema**，"
            "让输出成为可校验的契约而不是碰运气。"
            "selector 支持 iframe 穿透（iframe选择器 >>> tbody tr）。"
        ),
    },
    {
        "type": "browser.fetch",
        "key_fields": "targetUrl, selector, extractMode",
        "output_var_field": "outputVariable（提取结果存入该变量）",
        "description": (
            "轻量抓取：直接请求 targetUrl 并用 selector 提取内容，无需打开持久浏览器会话，"
            "适合静态/服务端渲染页面的一次性抓取。extractMode: text|html|attribute。"
            "注意地址字段是 targetUrl（不是 url）。需要点击/填表等交互时改用 browser.open + browser.* 序列"
        ),
    },
    {
        "type": "browser.wait",
        "key_fields": "selector",
        "output_var_field": "（无输出变量）",
        "description": "等待元素出现，需 selector。等待可选元素时加 continueOnError: true，超时不中断流程。",
    },
    {
        "type": "browser.waitFor",
        "key_fields": "selector, waitCondition（visible|hidden|textContains）, inputValue（textContains 时的期望文本）",
        "output_var_field": "（无输出变量）",
        # 这条能力过去没写进清单，模型于是只能用 delayMs 猜 loading 要转多久——
        # 「等某个东西消失」browser.wait 表达不了，缺了它盲等就是唯一写法。
        "description": (
            "等待条件成立：hidden 等元素消失（loading 遮罩、骨架屏），"
            "textContains 等元素文本出现期望内容（异步渲染的结果文案、状态变为「已完成」）。"
            "点击后要等页面就绪时用它，不要用 delayMs 猜耗时。"
        ),
    },
    {
        "type": "browser.scroll",
        "key_fields": "direction, distance",
        "output_var_field": "（无输出变量）",
        "description": "滚动页面，direction: down|up|left|right",
    },
    {
        "type": "browser.select",
        "key_fields": "selector, inputValue",
        "output_var_field": "（无输出变量）",
        "description": "选择下拉框选项，inputValue 为选项的 value 属性",
    },
    {
        "type": "browser.screenshot",
        "key_fields": "",
        "output_var_field": "outputVariable（截图 base64 存入该变量）",
        "description": "截取当前页面截图",
    },
    {
        "type": "browser.press",
        "key_fields": "selector, inputValue",
        "output_var_field": "（无输出变量）",
        "description": "模拟按键，inputValue 填写按键名如 Enter/Tab/Escape",
    },
    {
        "type": "browser.dismiss",
        "key_fields": "",
        "output_var_field": "（无输出变量）",
        "description": "关闭浏览器原生 alert/confirm/prompt 弹框。必须加 continueOnError: true，因为弹框不一定出现。",
    },
    {
        "type": "browser.check",
        "key_fields": "selector, checked",
        "output_var_field": "（无输出变量）",
        "description": "勾选/取消复选框，checked: true|false",
    },
    {
        "type": "browser.paginateNext",
        "key_fields": "selector, targetSelector（点击式）／urlTemplate, targetSelector（URL 式）",
        "output_var_field": "outputVariable（逐页累计提取到的内容列表，可选）",
        "description": (
            "逐页抓取，两种模式二选一。"
            "**点击式**：selector 指向「下一页」按钮，翻一页抽一页；按钮消失/禁用即停。"
            "**URL 式**：填 urlTemplate（含 `${page}` 占位，如 `https://x.com/list?p=${page}`）并**不要填 selector**，"
            "逐页直接换地址进入，本页抽不到内容或与上一页逐字相同即停；可选 startPage（默认 1，有的站从 0 开始）、"
            "pageStep（默认 1；offset 型分页如 `?start=${page}` 配 startPage=0 + pageStep=20）。"
            "**数字页码站点（1 2 3 … 下一页）必须用 URL 式**：点到第 2 页后页码控件位置就变了，点击式会当场失效。"
            "两种模式都用 targetSelector 提取本页内容，结果累计存入 outputVariable，页数存入 pageCountVariable；"
            "支持 outputSchema（同 browser.extract）"
        ),
    },
    {
        "type": "browser.clickLoadMore",
        "key_fields": "selector, targetSelector",
        "output_var_field": "outputVariable（累计提取到的全部内容列表，可选）",
        "description": "反复点击“加载更多”按钮（selector）直到无更多，期间用 targetSelector 累计提取内容；适合无限滚动/瀑布流页面，结果存入 outputVariable。支持 outputSchema（同 browser.extract）",
    },
    {
        "type": "browser.drag",
        "key_fields": "selector, targetSelector",
        "output_var_field": "（无输出变量）",
        "description": "拖拽元素，selector 为被拖拽元素，targetSelector 为放置目标位置",
    },
    {
        "type": "browser.tab.open",
        "key_fields": "targetUrl",
        "output_var_field": "（无输出变量）",
        "description": "打开新标签页，targetUrl 为新标签页要加载的地址（可留空仅开空白页）",
    },
    {
        "type": "browser.tab.switch",
        "key_fields": "index",
        "output_var_field": "（无输出变量）",
        "description": "切换到指定标签页，index 为从 0 起的标签页序号（注意字段名是 index，不是 tabIndex）",
    },
    {
        "type": "browser.tab.close",
        "key_fields": "",
        "output_var_field": "（无输出变量）",
        "description": "关闭当前标签页",
    },
    {
        "type": "browser.hover",
        "key_fields": "selector",
        "output_var_field": "（无输出变量）",
        "description": (
            "鼠标悬停在元素上（hover），用于触发悬停展开的下拉菜单或二级菜单（如 Element UI NavMenu）。"
            "selector 同 browser.click。可选 delayMs（悬停后等待毫秒，默认 0）。"
            "悬停后再用 browser.click 点击展开的子菜单项。"
        ),
    },
    # UI 别名节点 (kind: ui)
    {
        "type": "ui.click / ui.fill / ui.wait / ui.extract / ui.screenshot / ui.select / ui.check / ui.drag",
        "key_fields": "（与去掉 ui. 前缀后的同名 browser.* 节点完全一致）",
        "output_var_field": "（与对应 browser.* 节点一致）",
        "description": (
            "browser.* 同名节点的等价别名（运行时逐一映射为 browser.click / browser.fill 等），"
            "字段、行为、校验规则完全相同。仅用于兼容存量流程中已有的 ui.* 节点——"
            "修改这类节点时保留原 type 即可；**新建流程一律使用 browser.* 类型**，不要混用两套前缀。"
        ),
    },
    # 流程控制 (kind: control)
    {
        "type": "control.foreach",
        "key_fields": "itemsVariable, itemVariable",
        "output_var_field": "itemVariable（循环体内可用的当前项变量名）",
        "description": (
            "遍历列表变量，itemsVariable 为源列表变量名，itemVariable 为当前项变量名。"
            "出边必须加 label：循环体出边 label='body'，循环后出边 label='exit'。"
            "循环体内节点用普通边顺序连接，不需要边回到 foreach 节点。"
        ),
    },
    {
        "type": "control.repeat_until",
        "key_fields": "condition, maxIterations, indexVariable",
        "output_var_field": "indexVariable（已执行轮数，默认 repeat_index）",
        "description": (
            "重复执行循环体，直到 condition 成立。**次数由运行时状态决定时用它**——"
            "日历面板翻到目标月份、点「加载更多」到按钮消失、轮询等待状态变化，"
            "这些次数在生成流程时算不出来，展开成固定条数的节点链只在生成当天成立。"
            "condition 与 control.condition 同语法（裸变量名，如 panel_month == '2026-06'），"
            "在每轮**开始前**求值：已经满足就一次都不执行。循环体里必须有节点更新条件里的变量"
            "（如重新 extract 面板标题），否则会一直跑到上限。"
            "出边 label 规则与 control.foreach 相同：循环体 label='body'，循环后 label='exit'。"
            "跑满 maxIterations 仍未满足条件会让运行失败（这说明目标状态没达成）；"
            "确实允许「尽力而为」时才设 continueOnMaxIterations=true。"
        ),
    },
    {
        "type": "control.condition",
        "key_fields": "inputValue",
        "output_var_field": "（无输出变量）",
        "description": "条件分支（if/else），inputValue 为布尔表达式，只写裸变量名如 login_status == 'login_required'；写 ${var.xxx} 会被 lint 判 error",
    },
    {
        "type": "control.delay",
        "key_fields": "delayMs",
        "output_var_field": "（无输出变量）",
        "description": "延时等待，delayMs 为毫秒数",
    },
    {
        "type": "control.retry",
        "key_fields": "retryCount, delayMs",
        "output_var_field": "（无输出变量）",
        "description": "自动重试，retryCount 次数，delayMs 间隔",
    },
    {
        "type": "control.try",
        "key_fields": "errorVariable",
        "output_var_field": "errorVariable（异常信息字符串存入该变量，必填）",
        "description": "异常捕获块，catch 分支可读 errorVariable",
    },
    {
        "type": "control.break",
        "key_fields": "",
        "output_var_field": "（无输出变量）",
        "description": "跳出当前循环",
    },
    {
        "type": "control.noop",
        "key_fields": "",
        "output_var_field": "（无输出变量）",
        "description": (
            "空操作占位节点，不执行任何动作。**不要用它当分支落点**——"
            "条件节点的 false 边直接连汇合节点即可，多加一个 noop 只是徒增节点，"
            "编排层会在写入时自动摘掉这类直通 noop。"
        ),
    },
    {
        "type": "control.human_takeover",
        "key_fields": "title, message, timeoutMs",
        "output_var_field": "（无输出变量）",
        "description": "暂停流程等待人工接管：流程进入 paused_for_human 状态，桌面弹出操作卡片。title（必填，6字以内）显示为卡片标题（如「请完成滑块验证」「请扫码登录」）；message（必填）两句话：① 原因句说明为何暂停/检测到什么（如「检测到极验滑块验证，自动化无法通过」），② 操作句告知用户在浏览器中要做什么——禁止写泛化描述「请完成手动操作」，禁止写 UI 导航指引「点击继续/橙色横幅」；timeoutMs 默认 600000（10 分钟），超时按任务停止（非失败）处理。",
    },
    {
        "type": "control.subprocess",
        "key_fields": "flowId",
        "output_var_field": "（无输出变量）",
        "description": "调用子流程，flowId 为目标流程 ID",
    },
    # 变量 / 消息 (kind: variable)
    {
        "type": "variable.set",
        "key_fields": "variableName, value",
        "output_var_field": "variableName（被设置的变量名，后续节点可引用）",
        "description": "设置/修改变量，variableName 为目标变量名，value 可含 ${var.xxx}",
    },
    {
        "type": "variable.get",
        "key_fields": "variableName",
        "output_var_field": "outputVariable（读取值存入该变量）",
        "description": "读取变量值到另一个变量",
    },
    {
        "type": "variable.input",
        "key_fields": "message（提示文字）, variableName（存储变量名，必填）",
        "output_var_field": "variableName（用户输入存入该变量，必填；注意：不是 outputVariable）",
        "description": "弹出输入框等待用户输入，message 为提示文字，variableName 为存储变量名",
    },
    {
        "type": "variable.log",
        "key_fields": "message",
        "output_var_field": "（无输出变量）",
        "description": "记录日志，message 可含 ${var.xxx}",
    },
    {
        "type": "variable.notify",
        "key_fields": "channel, message",
        "output_var_field": "outputVariable（通知发送状态，可选）",
        "description": "发送通知，channel 为通知渠道名，message 可含 ${var.xxx}",
    },
    {
        "type": "variable.clipboard",
        "key_fields": "content",
        "output_var_field": "outputVariable（剪贴板内容，可选）",
        "description": "读取或写入剪贴板，content 填写时为写入；留空时读取当前剪贴板内容到 outputVariable",
    },
    # 数据处理 (kind: data)
    {
        "type": "data.json.parse",
        "key_fields": "inputVariable",
        "output_var_field": "outputVariable（解析结果存入该变量，必填）",
        "description": "解析 JSON 字符串为对象，inputVariable 为含 JSON 的变量名",
    },
    {
        "type": "data.string.transform",
        "key_fields": "inputVariable, operation",
        "output_var_field": "outputVariable（转换结果存入该变量，必填）",
        "description": "字符串操作：trim/upper/lower/replace/split 等",
    },
    {
        "type": "data.regex.match",
        "key_fields": "inputVariable, pattern",
        "output_var_field": "outputVariable（匹配结果列表存入该变量，必填）",
        "description": "正则匹配，inputVariable 为源变量名，pattern 为正则表达式",
    },
    {
        "type": "data.list.map",
        "key_fields": "inputVariable, operation",
        "output_var_field": "outputVariable（处理结果存入该变量，必填）",
        "description": "列表处理，operation: compact（去空）| unique（去重）| join（合并为字符串，需 delimiter）",
    },
    {
        "type": "data.math.compute",
        "key_fields": "left, right, operator",
        "output_var_field": "outputVariable（计算结果存入该变量，必填）",
        "description": "数学计算，left/right 可用 ${var.xxx}，operator: add|subtract|multiply|divide|mod",
    },
    {
        "type": "data.convert",
        "key_fields": "inputValue, operation",
        "output_var_field": "outputVariable（转换结果存入该变量，必填）",
        "description": "类型转换，operation: to_int | to_float | to_bool | to_str | to_list | to_json；inputValue 可含 ${var.xxx}",
    },
    {
        "type": "data.encrypt",
        "key_fields": "inputValue, operation",
        "output_var_field": "outputVariable（结果存入该变量，必填）",
        "description": (
            "哈希/加密/编解码，operation: md5 | sha256 | sha1 | base64_encode | base64_decode | aes_encrypt | aes_decrypt；"
            "AES 操作需在 pattern 字段填写密钥字符串（缺省使用内置默认密钥）"
        ),
    },
    # HTTP 请求 (kind: http)
    {
        "type": "http.request",
        "key_fields": "url, method",
        "output_var_field": "outputVariable（响应体存入该变量，必填）",
        "description": "发起 HTTP 请求，method: GET|POST|PUT|DELETE",
    },
    # 脚本 (kind: script)
    {
        "type": "script.python",
        "key_fields": "code（内联代码，默认）或 path+code（本地文件模式，首次运行自动生成）, inputVariables（必填）",
        "output_var_field": "outputVariable（脚本 stdout 存入该变量，可选）",
        "description": (
            "执行 Python 脚本。默认用 code 字段写内联代码；"
            "若用户需要在本地查看/编辑脚本文件，同时填 path（相对路径如 scripts/run.py）和 code（初始内容），"
            "首次运行时自动在工作区生成该文件，后续执行用户修改后的版本。"
            "path 只能是相对路径，工作区根目录为 ~/.easy-rpa/workspace/。"
            "【关键】脚本是独立子进程，流程变量必须从环境变量读取："
            "import json,os; _v=json.loads(os.environ.get('RPA_VARIABLES_JSON','{}')); val=_v.get('变量名','')"
            "——直接写变量名（如 data=my_var）会报 NameError。"
            "节点必须用 inputVariables 精确列出读取的业务变量；未声明的流程变量不会注入子进程。"
            "【输出文件】产物写到 _v['output_dir'] 下、文件名带 _v['run_timestamp']（如 "
            "os.path.join(_v['output_dir'], 'data_%s.json' % _v['run_timestamp'])），先 os.makedirs(_v['output_dir'], exist_ok=True)。"
            + describe_script_capabilities()
        ),
    },
    {
        "type": "script.javascript",
        "key_fields": "code（内联代码，默认）或 path+code（本地文件模式，首次运行自动生成）, inputVariables（必填）",
        "output_var_field": "outputVariable（脚本 stdout 存入该变量，可选）",
        "description": (
            "执行 JavaScript 脚本。默认用 code 字段写内联代码；"
            "若用户需要本地可编辑文件，同时填 path（.js 相对路径）和 code（初始内容），首次运行自动生成。"
        ),
    },
    {
        "type": "script.shell",
        "key_fields": "command, inputVariables（必填；不读取业务变量时为空数组）",
        "output_var_field": "outputVariable（命令 stdout 存入该变量，可选）",
        "description": "执行 shell 命令，command 可含 ${var.xxx}",
    },
    {
        "type": "script.websocket",
        "key_fields": "url, message",
        "output_var_field": "outputVariable（接收到的响应内容存入该变量，可选）",
        "description": "连接 WebSocket，发送 message 后等待一条响应消息，url 需以 ws:// 或 wss:// 开头",
    },
    # 文件 / Excel (kind: file / excel)
    {
        "type": "file.read",
        "key_fields": "path",
        "output_var_field": "outputVariable（文件内容存入该变量，必填）",
        "description": "读取文件内容到变量，path 为相对工作区或绝对路径",
    },
    {
        "type": "file.write",
        "key_fields": "path, content",
        "output_var_field": "（无输出变量）",
        "description": "将内容写入文件，若目录不存在会自动创建，content 可含 ${var.xxx}",
    },
    {
        "type": "file.copy",
        "key_fields": "path, targetPath",
        "output_var_field": "（无输出变量）",
        "description": "复制文件，path 为源，targetPath 为目标",
    },
    {
        "type": "file.move",
        "key_fields": "path, targetPath",
        "output_var_field": "（无输出变量）",
        "description": "移动/重命名文件",
    },
    {
        "type": "file.delete",
        "key_fields": "path",
        "output_var_field": "（无输出变量）",
        "description": "删除文件",
    },
    {
        "type": "file.list",
        "key_fields": "path",
        "output_var_field": "outputVariable（文件列表存入该变量，必填）",
        "description": "列出目录中的文件，pattern 可过滤（如 *.csv）",
    },
    {
        "type": "file.compress",
        "key_fields": "path, targetPath, operation",
        "output_var_field": "outputVariable（输出文件路径，可选）",
        "description": "压缩或解压文件，operation: compress（默认）| decompress；targetPath 后缀决定格式（.zip / .tar.gz）",
    },
    {
        "type": "file.rename",
        "key_fields": "path, targetPath",
        "output_var_field": "outputVariable（新路径，可选）",
        "description": "重命名或移动文件，path 为源路径，targetPath 为新路径",
    },
    {
        "type": "file.watch",
        "key_fields": "path, pattern",
        "output_var_field": "outputVariable（新增文件列表存入该变量，必填）",
        "description": "监视目录，等待新文件出现后返回文件列表；pattern 为 glob 过滤规则（默认 *），超时则报错",
    },
    {
        "type": "excel.read",
        "key_fields": "path, sheetName",
        "output_var_field": "outputVariable（读取数据存入该变量，必填）",
        "description": "读取 Excel 工作表数据为列表",
    },
    {
        "type": "excel.write",
        "key_fields": "path, sheetName, cellAddress, value",
        "output_var_field": "（无输出变量）",
        "description": "写入 Excel 单元格",
    },
    {
        "type": "excel.addrow",
        "key_fields": "path, sheetName, rowData",
        "output_var_field": "（无输出变量）",
        "description": "向 Excel 追加一行，rowData 为字典或列表",
    },
    {
        "type": "excel.save",
        "key_fields": "path",
        "output_var_field": "（无输出变量）",
        "description": "保存 Excel 文件",
    },
    {
        "type": "excel.deleterow",
        "key_fields": "path, sheetName, rowIndex",
        "output_var_field": "（无输出变量）",
        "description": "删除 Excel 指定行",
    },
    {
        "type": "excel.filter",
        "key_fields": "path, sheetName, filterExpression",
        "output_var_field": "outputVariable（过滤结果存入该变量，必填）",
        "description": "按条件过滤 Excel 行",
    },
]


def select_node_types(types: list[str] | None) -> dict[str, object]:
    """按需返回节点详情；空查询只给名称索引，避免把整份目录塞进模型上下文。"""
    available = [entry["type"] for entry in NODE_TYPE_CATALOG]
    requested = list(dict.fromkeys(str(item).strip() for item in (types or []) if str(item).strip()))
    if not requested:
        return {
            "available_types": available,
            "node_types": [],
            "message": "请用 types 指定需要查询的节点类型，单次最多 8 个。",
        }
    wanted = set(requested[:8])
    matches = [entry for entry in NODE_TYPE_CATALOG if entry["type"] in wanted]
    found = {entry["type"] for entry in matches}
    return {
        "node_types": matches,
        "unknown_types": [item for item in requested[:8] if item not in found],
        "truncated": len(requested) > 8,
    }
