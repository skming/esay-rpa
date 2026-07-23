"""暴露给模型的工具 JSON Schema（OpenAI tool 格式）。"""
from __future__ import annotations

from typing import Any

# Tool JSON Schemas (OpenAI tool format)

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "lint_flow",
            "description": (
                "对流程进行全面静态质量检查（与 AI 模型无关的程序化扫描），发现以下问题并返回结构化 findings：\n"
                "  • 孤儿节点（无法从起点到达，运行时被跳过）\n"
                "  • foreach 节点缺少 body/exit 标签出边（循环逻辑断路）\n"
                "  • condition 节点缺少 true/false 分支边（分支逻辑断路）\n"
                "  • browser.extract 缺少 outputVariable 或 extractMode（结果丢失）\n"
                "  • http.request 缺少 outputVariable（响应无法被引用）\n"
                "  • variable.input 误用于账号密码等凭据字段（每次运行暂停等手动输入）\n"
                "  • 输出文件路径无时间戳（覆盖上次结果）\n"
                "  • browser.*/select 等节点缺少 selector（运行时崩溃）\n"
                "每条 finding 包含 severity（error/warn）、node_id、issue 类型、message 和 fix 建议。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string", "description": "要检查的流程 ID"}
                },
                "required": ["flow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_node_types",
            "description": "列出所有可用的 RPA 节点类型、关键字段及其输出变量字段，在构建或修改流程前调用以了解能力边界。",
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_flow",
            "description": (
                "获取流程完整结构（节点列表、连线、input_variables），修改前必须先调用。\n"
                "⚠️ 向已有流程添加节点时，必须先读取返回的 input_variables，"
                "新节点直接引用已有变量名（如 ${var.username}），禁止为同一概念重复声明不同名称的变量。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string", "description": "流程 ID"}
                },
                "required": ["flow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_flow",
            "description": (
                "扫描流程的变量依赖关系，返回：\n"
                "  • defined_variables — 所有已定义变量（含输入变量和运行时内置）\n"
                "  • issues — 每个引用了未定义变量的节点及缺失变量名\n"
                "  • is_valid — 无问题时为 true"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string"}
                },
                "required": ["flow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_flows",
            "description": "列出所有已有流程（id、名称、状态）。",
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_flow",
            "description": (
                "创建**全新**的 RPA 流程，返回新流程 ID。\n"
                "⚠️ 仅在用户明确要求创建新流程时调用。已有流程需修改时用 update_flow，不要重复创建同名流程。\n"
                "⚠️ 调用前确认已了解目标 URL、是否需要登录、要提取的内容、输出格式。信息不完整时先提问。\n"
                "节点格式：type 为点分格式（browser.open）；所有配置字段平铺在根层，不嵌套在 config 中；\n"
                "必填公共字段：id、type、title、kind、status(pending)、position({x,y})、description。\n"
                "连线 id 格式：e_{source}_{target}。foreach 出边必须加 label：body / exit。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "流程名称"},
                    "description": {"type": "string", "description": "流程描述"},
                    "nodes": {
                        "type": "array",
                        "description": "节点列表",
                        "items": {"type": "object"},
                    },
                    "edges": {
                        "type": "array",
                        "description": "连线列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "source": {"type": "string"},
                                "target": {"type": "string"},
                            },
                            "required": ["source", "target"],
                        },
                    },
                    "input_variables": {
                        "type": "array",
                        "description": (
                            "流程输入变量声明。每项字段：\n"
                            "  name: 变量名（英文）\n"
                            "  type: 类型，必须是以下之一（区分大小写）：String | Integer | Boolean | List | Dict\n"
                            "  value: 变量值字符串（可为空字符串；get_flow 读回来的也是这个字段名）\n"
                            "  category: 可选，flow（默认）| credential（账号/密码用这个）\n"
                            "  sensitive: 可选布尔，密码类变量设为 true\n"
                            "示例：{\"name\":\"username\",\"type\":\"String\",\"value\":\"\",\"category\":\"credential\"}\n"
                            "示例：{\"name\":\"password\",\"type\":\"String\",\"value\":\"\",\"category\":\"credential\",\"sensitive\":true}"
                        ),
                        "items": {"type": "object"},
                    },
                },
                "required": ["name", "nodes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_flow",
            "description": (
                "直接修改现有流程的节点或连线并立即写入，无需用户确认。\n"
                "适用场景：增删节点、调整连线、批量修改多个节点。\n"
                "系统会自动检测并清理因插入节点导致的旧边冲突。\n"
                "调用前先通过 get_flow 了解当前结构（包括 edges）。修改后若涉及变量引用变化，也应调用 validate_flow 验证。\n\n"
                "【调用后必须核查响应】\n"
                "  返回体中的 updated_node_snapshots 列出了每个被 update_nodes 修改节点的 patched_fields 实际值。\n"
                "  ⚠️ 必须逐项确认 patched_fields 与你的修改意图一致，不一致则重新调用修正，绝不能假设修改成功后直接运行。\n"
                "  修改后若 lint_findings 仍包含同一节点的同一 issue，说明修复未生效，需要重新检查 patch 格式再试。\n\n"
                "【add_nodes vs update_nodes 区别（必须严格区分）】\n"
                "  • add_nodes：只用于添加全新节点。若 id 与流程中已有节点冲突，服务器会报错拒绝。\n"
                "    ⚠️ 'start'、'end' 等已存在的节点绝不能放入 add_nodes——必须用 update_nodes 修改\n"
                "  • update_nodes：修改已存在节点的字段，每项含 {id, patch}，patch 只写要改的字段\n\n"
                "【连线管理规则】\n"
                "  add_edges 的 source/target 只能引用已存在的节点或本次 add_nodes 同时新建的节点 id——"
                "严禁连到尚未创建的节点。删除某节点的入边时，必须同时补上新的入边，"
                "否则其下游分支会变成不可达孤儿节点（系统会返回 connectivity_warning，你必须继续补连）。\n"
                "foreach 节点出边必须加 label：循环体边 label='body'，循环后边 label='exit'。\n\n"
                "【流程命名】\n"
                "  若当前流程名仍是占位名（如「新建 RPA 流程」「未命名流程」），"
                "在把流程内容构建完整后，应顺带传入 name 参数，"
                "根据用户需求和已搭建的节点内容起一个简洁准确的标题（无需用户确认）；"
                "流程已有恰当名称时不要覆盖。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string"},
                    "name": {
                        "type": "string",
                        "description": "流程名称。仅当当前名称是占位名（新建 RPA 流程/未命名流程）时才需要传入并覆盖。",
                    },
                    "add_nodes": {
                        "type": "array",
                        "description": "新增节点列表",
                        "items": {"type": "object"},
                    },
                    "update_nodes": {
                        "type": "array",
                        "description": "修改节点，每项包含 id 和 patch 字段",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "patch": {"type": "object"},
                            },
                            "required": ["id", "patch"],
                        },
                    },
                    "remove_node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "删除的节点 ID 列表",
                    },
                    "add_edges": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "新增连线",
                    },
                    "remove_edge_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "删除的连线 ID 列表",
                    },
                },
                "required": ["flow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_flow",
            "description": (
                "运行指定流程并等待执行完成（内部自动轮询，最长等待 90 秒），"
                "直接返回最终 status（success / error / stopped / timeout）和 task_id。"
                "success → 调用 get_run_output；error → 调用 get_run_error；"
                "timeout 且含 variable.input → 流程等待用户输入，禁止重新调用 run_flow。"
                "browser_executor='extension' 时会先检查扩展连接状态：未连接直接返回"
                "status=extension_not_connected 并阻止运行，不会静默回退到 Playwright。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string"},
                    "variables": {
                        "type": "object",
                        "description": "运行时变量覆盖（键值对）",
                    },
                    "browser_executor": {
                        "type": "string",
                        "enum": ["playwright", "extension"],
                        "description": (
                            "本次运行使用的浏览器执行器，默认 playwright（无人值守后台浏览器）。"
                            "用户明确要求'用 Chrome 扩展'或'复用真实登录态'时传 extension——"
                            "切勿把这个当成普通流程变量塞进 variables，那样对运行没有任何效果。"
                        ),
                    },
                },
                "required": ["flow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_extension_connection",
            "description": (
                "查询 Chrome 扩展桥接的实时连接状态。在用户要求使用扩展执行器"
                "（browser_executor='extension'）运行流程前必须先调用一次；"
                "未连接时应停止并提示用户先打开扩展、确保有已登录的标签页，而不是直接尝试运行。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_run",
            "description": (
                "停止一个正在运行或暂停等待中的任务。适用场景："
                "① 用户明确要求停止/取消当前运行；"
                "② run_flow 超时且流程含 variable.input / control.human_takeover，"
                "用户表示不想继续等待——先 stop_run 清理后台任务，再修复流程，"
                "避免旧任务一直占着浏览器等输入。"
                "禁止用它掩盖失败：任务已经 error/success 结束时调用无效果。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "要停止的任务 ID（run_flow 返回的 task_id）"}
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_schedules",
            "description": (
                "列出全部定时任务（schedule_id、名称、cron 表达式、时区、启用状态、"
                "关联 flow_id、下次/上次运行时间）。创建新定时任务前先调用，避免重复创建。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_schedule",
            "description": (
                "为已保存的流程创建定时任务（cron 调度）。用户说'每天早上9点自动跑'"
                "'每小时执行一次'等诉求时使用。cron_expression 为 5 段标准 Cron"
                "（分 时 日 月 周，如 '0 9 * * *' 表示每天 09:00）。"
                "创建前必须确认流程存在且可运行（凭据类 input_variables 的 value 不能为空——"
                "定时任务无人值守，无法运行时补输入）；含 variable.input / control.human_takeover "
                "的流程不适合定时执行，必须先提醒用户。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string", "description": "要定时运行的流程 ID"},
                    "name": {"type": "string", "description": "定时任务名称，默认为流程名"},
                    "cron_expression": {
                        "type": "string",
                        "description": "5 段 Cron 表达式（分 时 日 月 周），如 '0 9 * * *'（每天 09:00）、'*/30 * * * *'（每 30 分钟）",
                    },
                    "timezone": {"type": "string", "description": "IANA 时区名，默认 Asia/Shanghai"},
                    "enabled": {"type": "boolean", "description": "创建后是否立即启用，默认 true"},
                    "variables": {"type": "object", "description": "运行时变量覆盖（键值对），默认空"},
                    "browser_executor": {
                        "type": "string",
                        "enum": ["playwright", "extension"],
                        "description": (
                            "定时运行使用的浏览器执行器；不传时沿用流程的默认执行器设置。"
                            "extension 模式依赖一个已打开且已登录的真实浏览器窗口，"
                            "定时触发时窗口未开会导致该次执行失败——选它之前必须提醒用户。"
                        ),
                    },
                },
                "required": ["flow_id", "cron_expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_schedule",
            "description": (
                "启用或停用一个已有定时任务（不删除，可随时恢复）。"
                "用户要求'暂停这个定时任务''重新启用'时使用；"
                "用户要求彻底删除时引导其到任务中心手动删除（AI 不执行删除操作）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "schedule_id": {"type": "string"},
                    "enabled": {"type": "boolean", "description": "true=启用，false=停用"},
                },
                "required": ["schedule_id", "enabled"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_run_status",
            "description": "手动查询运行任务的状态（running/success/error）和进度百分比。通常无需调用，run_flow 已等待完成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"}
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_run_error",
            "description": (
                "获取运行失败的完整诊断：failed_node_id、run_error、error_logs、failed_node_config、"
                "last_browser_url、inspect_hint（selector 超时时存在）。\n"
                "navigation_trace 给出每个导航节点「请求了哪个 URL、实际停在哪个 URL」；"
                "其中任一条 redirected=true 即表示导航没到目标页，"
                "后续节点是在错误页面上找元素，改它们的 selector/delayMs 无效。\n"
                "仅用于流程已启动后的运行时错误；运行前错误（'引用了未定义变量'等）用 validate_flow。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"}
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_node_fix",
            "description": (
                "直接修改单个节点的配置字段（selector、outputVariable、path 等），立即写入，无需用户确认。\n"
                "⚠️ 写入后不会出现任何确认按钮——修复完成后直接告知用户结果，不要让用户'点击应用变更'。\n"
                "适用场景：修复单个节点的字段错误（如补全 path、修正 selector）。\n"
                "多节点结构性变更（增删节点、调整连线）请改用 update_flow。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string"},
                    "node_id": {"type": "string"},
                    "config_patch": {
                        "type": "object",
                        "description": "需要更新的配置字段键值对",
                    },
                },
                "required": ["flow_id", "node_id", "config_patch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_flow",
            "description": "将流程发布为 active 状态，使其可被调度执行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string"}
                },
                "required": ["flow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_run_output",
            "description": (
                "获取已完成任务的输出结果：输出变量快照和采集产物列表。\n"
                "在 get_run_status 返回 status=success 后调用，用于向用户汇报实际运行结果。\n"
                "返回：variables（变量名→值）、artifacts（产物文件名列表）、summary（简要描述）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"}
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assert_run_output",
            "description": (
                "对已成功运行的流程做通用质量审计。注意：run_flow status=success 只代表节点未报错，"
                "不代表流程结构、筛选链路、抽取形态和输出内容可信。抓取/筛选/导出类流程在 get_run_output 后必须调用本工具。\n\n"
                "本工具不是针对某个页面的校验器，而是模型无关的质量闸门：\n"
                "  • 结合流程 lint 发现高风险结构问题（如日期 fill、表格未 table mode、下拉关闭错误）\n"
                "  • 自动识别输出变量里的表格候选，检查是否按行结构化，而不是整张表拼成一个文本数组\n"
                "  • 可选使用 requirement_text 辅助推断用户约束；也可传入通用显式约束\n"
                "  • 比对需求里的业务目标词是否在输出的表头/数据里出现，识别「抓到了一张表但抓错了表」\n"
                "结构校验通过不等于抓对了数据：务必读 sample_rows 并与需求逐条核对后再汇报。\n"
                "审计失败时必须继续诊断和修复流程，禁止向用户报告“已成功完成”。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "要检查的运行任务 ID"},
                    "requirement_text": {
                        "type": "string",
                        "description": (
                            "用户原始需求文本，可选。工具会从中辅助推断日期、枚举、数量等约束，但不会绑定具体页面。"
                            "系统会用本会话用户原话覆盖此字段——需求以用户说的为准，写你自己的任务复述没有意义。"
                        ),
                    },
                    "min_rows": {"type": "integer", "description": "最少结果行数，可选"},
                    "max_rows": {"type": "integer", "description": "最多结果行数，可选"},
                    "date_field": {"type": "string", "description": "日期字段名或列名，可选；未知时可不传，由 AI 根据输出/需求继续诊断"},
                    "start_date": {"type": "string", "description": "起始日期 YYYY-MM-DD，可选"},
                    "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD，可选"},
                    "enum_field": {"type": "string", "description": "枚举字段名或列名，可选；未知时可不传，由 AI 根据输出/需求继续诊断"},
                    "allowed_values": {
                        "type": "array",
                        "description": "枚举字段允许值",
                        "items": {"type": "string"},
                    },
                    "content_match_confirmed": {
                        "type": "boolean",
                        "description": (
                            "声明你已把 sample_rows 与用户需求逐条比对、确认抓到的就是用户要的数据。"
                            "仅在工具报出 output_content_may_not_match_requirement、"
                            "而你核对后确认输出无误时才传 true；不确定就修流程重跑，不要用它消警告。"
                            "本会话未报出该问题前传 true 会被系统忽略并按 false 处理。"
                        ),
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_page",
            "description": (
                "用 Playwright 访问指定 URL，提取页面上的交互元素（输入框、按钮、菜单项、表格头、下拉框等），"
                "返回结构化文本，无需图片分析即可获取精确 CSS 选择器。\n\n"
                "**何时使用**：\n"
                "• 构建流程前不确定选择器（尤其是 Element UI / Ant Design 等组件库）\n"
                "• 使用不支持图像分析的模型（如 DeepSeek），无法解读截图时\n"
                "• 登录后需要检查目标页面的表单/菜单结构\n\n"
                "**返回字段**：\n"
                "• inputs / buttons / selects：表单字段和按钮（含精确 selector）\n"
                "• links：页面所有有文字的链接（text/href/selector/cls）——AI 自行判断哪些是导航/操作入口\n"
                "• tables：含表头的表格（headers/container_selector/cls/row_selector）；"
                "**row_selector 已自动收窄到最近业务容器作用域，直接用于 browser.extract 的 selector 字段**\n"
                "• visible_options：当前已展开下拉弹层中的选项（仅在弹层打开时有值）\n"
                "• page_classes：页面上所有实际 CSS class（最多120个），用于识别真实框架/命名规律\n"
                "• page_layout：body 顶层结构元素数组（tag/cls/role/id/aria_label/html），动态反映页面实际布局——"
                "当 links/tables 为空时必须查看此字段的 html 片段以获取真实类名\n\n"
                "**注意**：此工具会使用持久化浏览器 Profile（含登录 Cookie），"
                "因此访问登录后的页面时无需再走登录流程。\n"
                "如有正在运行的任务占用浏览器，需等任务完成后再调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要检查的页面 URL",
                    },
                    "wait_selector": {
                        "type": "string",
                        "description": (
                            "等待该 CSS 选择器出现后再提取（强烈推荐用于 Vue/React 等 SPA 页面）。"
                            "若上次调用返回 warning（元素为空），必须在此次重试中指定 wait_selector，"
                            "如 'nav, table, [role=grid], [role=navigation], main'。"
                        ),
                    },
                    "scope_selector": {
                        "type": "string",
                        "description": "只在该选择器范围内提取元素，适合聚焦表单区域或弹窗（如 .search-form、.el-dialog）",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_screenshot",
            "description": (
                "用 Playwright 访问指定 URL 并截取页面截图，以图片形式返回给模型直接查看。\n\n"
                "**何时使用**（仅限支持视觉的模型）：\n"
                "• inspect_page 返回的 DOM 信息不足以判断页面状态（canvas 渲染、复杂弹层、视觉布局问题）\n"
                "• 同一 selector 已失败 2 次且 inspect_page 无法解释原因，需要亲眼确认页面长什么样\n"
                "• 需要确认筛选/操作后页面的实际视觉结果（如日期选择器是否真的选中了）\n\n"
                "**注意**：\n"
                "• inspect_page 依然是获取精确 selector 的首选；截图用于确认状态，不用于抄 selector\n"
                "• 使用持久化浏览器 Profile（含登录 Cookie）\n"
                "• 当前模型不支持图片时该工具会被阻止，请改用 inspect_page"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要截图的页面 URL",
                    },
                    "wait_selector": {
                        "type": "string",
                        "description": "等待该 CSS 选择器出现后再截图（SPA 页面强烈推荐）",
                    },
                    "full_page": {
                        "type": "boolean",
                        "description": "是否截取整个页面（默认只截取当前视口 1280x800）",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_run_logs",
            "description": "获取运行任务的日志，可按节点 ID 或日志级别过滤。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "node_id": {
                        "type": "string",
                        "description": "仅返回该节点的日志（可选）",
                    },
                    "level": {
                        "type": "string",
                        "enum": ["error", "warn", "info", "debug"],
                        "description": "日志级别过滤（可选）",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
]

