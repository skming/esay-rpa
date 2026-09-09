"""暴露给模型的工具 JSON Schema（OpenAI tool 格式）。

这里刻意比 executor 支持的能力少。`get_flow` / `lint_flow` / `validate_flow` /
`get_run_status` 回答的都是「现在是什么状态」，而 `ai_flow_state` 每轮
开头就把答案放进了消息尾部的状态块。留着它们只会让模型多花一轮去确认自己已经知道的事，
删掉的是那一轮，不是那份能力——executor 的方法还在，调用方从模型换成了状态构建器。
"""
from __future__ import annotations

from typing import Any

# Tool JSON Schemas (OpenAI tool format)

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_node_types",
            "description": (
                "按需返回指定节点的 key_fields、输出字段和能力边界。仅在字段不确定时调用，"
                "不要为确认已知节点或页面访问问题查询目录；单次最多 8 个类型。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 8,
                        "description": "需要查询的精确节点类型，例如 browser.extract、file.write",
                    }
                },
                "required": ["types"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_flow",
            "description": (
                "创建全新流程；已有 flow_id 时改用 update_flow。所有节点字段平铺在节点根层。"
                "acceptance_contract 必须绑定用户原文与交付变量。返回 flow_id、revision、"
                "changed_nodes 和带 blocks_run 标记的 lint_findings。凭据变量只声明空值，"
                "不得把账号、密码或 Token 写入参数。"
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
                            "  value: 普通变量默认值；凭据变量必须为空，秘密值由用户在输入变量面板配置\n"
                            "  category: 可选，flow（默认）| credential（账号/密码用这个）\n"
                            "  sensitive: 可选布尔，密码类变量设为 true\n"
                            "示例：{\"name\":\"username\",\"type\":\"String\",\"value\":\"\",\"category\":\"credential\"}\n"
                            "示例：{\"name\":\"password\",\"type\":\"String\",\"value\":\"\",\"category\":\"credential\",\"sensitive\":true}"
                        ),
                        "items": {"type": "object"},
                    },
                    "acceptance_contract": {
                        "type": "object",
                        "description": (
                            "运行前冻结的交付验收契约。requirements 必须逐条记录用户原文来源；"
                            "deliverables 每项必须明确 id、variable、kind、requirement_ids；"
                            "kind 可为 table/document/file/scalar，并按需求填写 required_fields、"
                            "date_ranges、allowed_values、unique_by、required_terms、forbidden_terms 等后置条件。"
                            "审计只验这里声明的交付变量，不再根据变量名猜测。"
                        ),
                        "properties": {
                            "requirements": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "description": {"type": "string"},
                                        "source_kind": {"type": "string", "enum": ["user", "product_default"]},
                                        "source_quote": {"type": "string"},
                                        "source_turn_id": {"type": "string"},
                                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                        "confirmed": {"type": "boolean"},
                                    },
                                    "required": ["id", "description", "source_kind", "confidence", "confirmed"],
                                },
                            },
                            "deliverables": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "variable": {"type": "string"},
                                        "kind": {"type": "string", "enum": ["table", "document", "file", "scalar"]},
                                        "required": {"type": "boolean"},
                                        "min_rows": {"type": "integer"},
                                        "max_rows": {"type": "integer"},
                                        "required_fields": {"type": "array", "items": {"type": "string"}},
                                        "date_ranges": {"type": "array", "items": {"type": "object"}},
                                        "allowed_values": {"type": "array", "items": {"type": "object"}},
                                        "unique_by": {"type": "array", "items": {"type": "string"}},
                                        "min_chars": {"type": "integer"},
                                        "required_terms": {"type": "array", "items": {"type": "string"}},
                                        "forbidden_terms": {"type": "array", "items": {"type": "string"}},
                                        "source_variables": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": (
                                                "文档正文取自哪些运行变量。kind=document 时必填："
                                                "审计用它验证正文里确实含有本次运行抓到的数据。"
                                            ),
                                        },
                                        "extensions": {"type": "array", "items": {"type": "string"}},
                                        "min_bytes": {"type": "integer"},
                                        "requirement_ids": {"type": "array", "items": {"type": "string"}},
                                        "numeric_ranges": {"type": "array", "items": {"type": "object"}},
                                        "field_formats": {"type": "array", "items": {"type": "object"}},
                                        "cross_field_assertions": {"type": "array", "items": {"type": "object"}},
                                        "sort_assertions": {"type": "array", "items": {"type": "object"}},
                                        "aggregate_assertions": {"type": "array", "items": {"type": "object"}},
                                        "expected_count_variable": {"type": "string"},
                                        "minimum_coverage_ratio": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                                    },
                                    "required": ["id", "variable", "kind", "requirement_ids"],
                                },
                            },
                        },
                        "required": ["requirements", "deliverables"],
                    },
                },
                "required": ["name", "nodes", "acceptance_contract"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_flow",
            "description": (
                "直接修改现有流程并写入。节点 id 与当前结构以状态块为准：add_nodes 只放新 ID，"
                "update_nodes 使用 {id, patch} 修改已有节点，结构变化同时维护入边和出边。返回 revision、"
                "changed_nodes、updated_node_snapshots、connectivity_warning 和 lint_findings；"
                "必须核对 patched_fields，且先修复 blocks_run=true 的 finding。占位流程名可随本次"
                "完整构建一起更新，已有准确名称不要覆盖。"
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
                "启动流程并最多等待 90 秒，返回 task_id、flow_revision、progress 和 status。"
                "status=success 时同时返回 acceptance_audit——平台按流程冻结的验收契约算出的结论，"
                "passed=true 才算交付达标，passed=false 按 repair_plan 修流程后重跑；"
                "error 后诊断；paused_for_human / waiting_for_user_input"
                "表示原任务仍在等待用户，禁止重新运行；timeout 可查询状态。extension 未连接时"
                "返回 extension_not_connected，被设置关闭时返回 extension_disabled，两者都不会回退到 Playwright。"
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
                "查询 Chrome 扩展桥接的实时状态，返回 enabled（设置里的开关）与 connected（是否有浏览器接上）。"
                "在用户要求使用扩展执行器（browser_executor='extension'）运行流程前必须先调用一次；"
                "enabled=false 时提示用户去「设置 · 浏览器插件」开启，connected=false 时提示用户打开 Chrome 扩展、"
                "确保有已登录的标签页——两种情形都应停止，而不是直接尝试运行。"
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
            "name": "get_run_error",
            "description": (
                "下钻运行失败的现场：error_logs、failed_node_config、last_browser_url、"
                "失败截图、inspect_hint（selector 超时时存在）。\n"
                "navigation_trace 给出每个导航节点「请求了哪个 URL、实际停在哪个 URL」；"
                "其中任一条 redirected=true 即表示导航没到目标页，"
                "后续节点是在错误页面上找元素，改它们的 selector/delayMs 无效。\n"
                "状态块已给出 status、失败原因和失败节点，不必为知道「跑成没成」调这个；"
                "要看现场才调。"
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
                "结构性变更请改用 update_flow：增删节点、调整连线，以及换节点 type——"
                "config_patch 里出现 id/type 会被直接拒绝。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string"},
                    "node_id": {"type": "string"},
                    "config_patch": {
                        "type": "object",
                        "description": "需要更新的配置字段键值对；不接受 id/type",
                    },
                },
                "required": ["flow_id", "node_id", "config_patch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_acceptance_contract",
            "description": (
                "仅在用户本轮明确改变交付目标或业务约束时，替换流程验收契约。"
                "普通节点修复不得调用此工具放宽验收条件；调用后流程 revision 递增，旧运行证据全部失效。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string"},
                    "acceptance_contract": {
                        "type": "object",
                        "description": "完整替换契约；requirements 的用户条款仍必须携带 source_quote 原文。",
                    },
                    "requirement_change_quote": {
                        "type": "string",
                        "description": "用户本轮原话中明确改变需求的连续片段，系统会核对。",
                    },
                },
                "required": ["flow_id", "acceptance_contract", "requirement_change_quote"],
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
                "读取任务的实际输出。返回 variables（变量名到值）、artifacts（含 filename/type 的对象列表）"
                "和 summary。它只展示产物本身；这次运行有没有通过验收，由 run_flow 返回的 "
                "acceptance_audit 给出，那份结论由平台自己算，不需要你调用任何工具。"
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
            "name": "inspect_page",
            "description": (
                "观察页面。给 url 就在持久化浏览器 Profile 里打开它；不给 url 则观察当前已打开的页面"
                "（点开的下拉、日历、弹窗都还在，不会重新加载），配合 interact_page 形成"
                "「看→操作→再看」的循环。浏览器收到 HTTP 错误时自动降级为 Scrapling 静态抓取（需要 url）。"
                "浏览器成功时返回客观 DOM 事实：inputs、buttons、selects、"
                "links、tables、visible_options、page_classes、page_layout 和 frames。构建或修复"
                "selector 时优先使用返回值；tables[].row_selector 可直接用于表格抽取。元素为空时"
                "结合 warning 以 wait_selector 重试，登录重定向时不得把登录页 DOM 当成目标页。"
                "降级成功时 inspection_source=scrapling_static，此时证据只覆盖纯 HTTP 通道。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "browser_executor": {
                        "type": "string", "enum": ["playwright", "extension"],
                        "description": "指定观察通道；省略沿用当前会话，初次默认 playwright。extension 读取 Chrome 当前活动网页和登录态，须省略 url；不支持 frame_selector/tab_index/full_page。",
                    },
                    "url": {
                        "type": "string",
                        "description": "要打开的页面 URL；省略则观察当前页面当前状态（必须已经打开过页面）",
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
                    "frame_selector": {
                        "type": "string",
                        "description": "改到该 iframe 内观察（传 'main' 回主文档）；后续 interact_page 也作用于该 frame",
                    },
                    "tab_index": {
                        "type": "integer",
                        "description": "先切到第几个标签页（0 起）；点击弹出新窗口后用它跟过去",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "interact_page",
            "description": (
                "在 inspect_page 打开的同一个页面上做一次操作，然后自动重新观察并返回变化。"
                "用它确认「点开日期面板后面板里有什么」「选完城市后区县选项变了没有」这类"
                "只在交互之后才存在的事实——这些 DOM 在页面加载时不存在，光调 inspect_page 永远看不到。\n"
                "目标优先用 element_ref（inspect_page 返回的 ref），它避开了同名按钮的歧义；"
                "ref 只在返回它的那次观察内有效，页面变过就要重新 inspect_page。\n"
                "返回分三层，不能互相替代：\n"
                "- effect.changed：整页有没有可观测变化。false 不代表失败（已聚焦的 fill、原生 select "
                "换选项、不新增 DOM 的滚动都不动它），true 也不代表业务成功。\n"
                "- action_effect.status：这个动作自己的目标状态。target_reached=已达到；"
                "already_in_target_state=本来就是这个状态（幂等，别换目标重试）；target_not_reached=回读"
                "证明没达到；state_changed=没有指定目标状态但状态确实变了；focus_only=只变了焦点；"
                "no_observable_change=什么都没观察到（是缺证据，不是失败，先确认目标元素与前置条件）；"
                "unknown=读不到目标状态。\n"
                "- wait_result（指定 wait_selector 时）：satisfied=等到目标；timed_out=等待条件超时；"
                "unknown=通道超时，无法确认条件。超时后仍返回可获取的当前观察，不要重复执行原动作来取证。\n"
                "- business_check：业务后置条件一律未验证。筛选真的生效、表单真的提交，只能靠抓回的"
                "数据断言，不能拿 effect.changed=true 当结论。\n"
                "注意：ref 是临时的，绝不能写进流程节点；流程里只能用验证过的稳定 selector。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["click", "fill", "select_option", "press", "hover", "scroll"],
                        "description": "press 时 value 是键名（如 Enter）；scroll 不需要目标，滚动整页",
                    },
                    "element_ref": {
                        "type": "string",
                        "description": "inspect_page 返回的元素 ref（如 e12），优先用它",
                    },
                    "selector": {
                        "type": "string",
                        "description": "没有 ref 时用 CSS 选择器；命中多个会被拒绝，需要收窄",
                    },
                    "value": {
                        "type": "string",
                        "description": "fill 的文本、select_option 的选项 value、press 的键名",
                    },
                    "observation_version": {
                        "type": "integer",
                        "description": "element_ref 来自哪次观察（inspect_page 返回的 observation_version）",
                    },
                    "scope_selector": {
                        "type": "string",
                        "description": "操作后重新观察时只看这个范围（可选）",
                    },
                    "wait_selector": {
                        "type": "string",
                        "description": "操作后等待该选择器出现再观察，比等固定时间可靠（如 .ant-picker-dropdown）",
                    },
                    "wait_ms": {
                        "type": "integer",
                        "description": "没有可等的选择器时才用，默认 600ms",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_screenshot",
            "description": (
                "在当前探索通道截取页面截图，以图片形式返回给模型直接查看。\n\n"
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
                    "browser_executor": {
                        "type": "string", "enum": ["playwright", "extension"],
                        "description": "指定观察通道；省略沿用当前会话，初次默认 playwright。extension 读取 Chrome 当前活动网页和登录态，须省略 url；不支持 frame_selector/tab_index/full_page。",
                    },
                    "url": {
                        "type": "string",
                        "description": "要打开的页面 URL；省略则截当前页面当前状态（与 inspect_page 同一个页面）",
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
