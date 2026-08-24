"""运行结果诊断：失败根因推断、输出数据质量断言、修复方案生成。

面向"流程跑完之后"的分析，与面向"跑之前"的 lint 互补。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.services.ai_tools.variables import _RUNTIME_BUILTINS
from app.services.node_semantics import TRANSFORM_NODE_TYPES

_NAVIGATION_NODE_TYPES = frozenset({"browser.open", "browser.ensureLogin"})

# 诊断类型名产在这里、消费在编排层与站点档案，必须引常量而不是各处写字面量：
# 名字对不上不会报错，只会让那条判据永远不命中，表现和"没写过这条判据"一模一样。
SELECTOR_ZERO_MATCH = "selector_zero_match"
SELECTOR_MULTI_MATCH_FIRST_NOT_ACTIONABLE = "selector_multi_match_first_not_actionable"
SELECTOR_MATCH_NOT_VISIBLE = "selector_match_not_visible"
SELECTOR_MATCH_HIDDEN_OR_NOT_VISIBLE = "selector_match_hidden_or_not_visible"

# 元素压根不在，或选得太宽以致选中的不是要操作的那个——这两类是 selector 本身写错了。
SELECTOR_FALSIFYING_KINDS = frozenset({
    SELECTOR_ZERO_MATCH,
    SELECTOR_MULTI_MATCH_FIRST_NOT_ACTIONABLE,
})

# 元素找到了但点不动，改 selector 一律无效（出路是 continueOnError / force / 等时机）。
# 两者不能各自判断：区别只在 Playwright 有没有在报错里报出匹配数量，成因与修法完全相同。
SELECTOR_NOT_VISIBLE_KINDS = frozenset({
    SELECTOR_MATCH_NOT_VISIBLE,
    SELECTOR_MATCH_HIDDEN_OR_NOT_VISIBLE,
})

SELECTOR_DIAGNOSTIC_KINDS = SELECTOR_FALSIFYING_KINDS | SELECTOR_NOT_VISIBLE_KINDS

# 加工节点的输出与输入无实质差别。名字产在这里、消费在 repair_plan 与编排层的汇报纠偏，
# 一律引常量：抄成字面量不会报错，只会让依赖它的那半条判据永远不命中
TRANSFORM_HAD_NO_EFFECT = "transform_had_no_effect"


def _read_log_url(log: Any) -> str | None:
    detail = str(getattr(log, "detail", "") or "")
    return detail if detail.startswith(("http://", "https://")) else None


def _route_key(url: str) -> str:
    """比到「路由位置」这一层：query 参数和末尾斜杠的差异不算换了页面。"""
    return url.split("?", 1)[0].rstrip("/")


def _is_bare_origin(url: str) -> bool:
    """只写了域名、没写具体路由。"""
    _, _, rest = _route_key(url).partition("://")
    return "/" not in rest and "#" not in rest


def _same_page(requested: str, landed: str) -> bool:
    want, got = _route_key(requested), _route_key(landed)
    if want == got:
        return True
    # 请求裸域名、落在应用默认路由（/#/index 之类）是 SPA 正常行为，不是导航失败。
    # 这条豁免只对裸域名成立——写了具体路由却落到别处，正是要抓的重定向。
    return _is_bare_origin(requested) and got.startswith(want)


def build_navigation_trace(all_logs: list[Any], nodes: list[Any]) -> list[dict[str, Any]]:
    """每个导航节点「想去哪、实际到了哪」的对照表。

    这是 AI 判断「导航方式是否被路由守卫拦了」的唯一硬证据，此前只能靠它自己
    翻日志逐条拼——而日志里 running 那条带请求 URL、success 那条带落地 URL，
    对照关系是确定的，没有理由让模型手工重建。
    """
    requested: dict[str, str] = {}
    landed: dict[str, str] = {}
    for log in all_logs:
        node_id = getattr(log, "node_id", None)
        url = _read_log_url(log)
        if not node_id or url is None:
            continue
        # running 先于 success 写入；取每个阶段的首条即可，重试不会覆盖原始意图
        bucket = requested if getattr(log, "level", None) == "running" else landed
        bucket.setdefault(str(node_id), url)

    trace: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") not in _NAVIGATION_NODE_TYPES:
            continue
        node_id = str(node.get("id") or "")
        if node_id not in requested and node_id not in landed:
            continue  # 本次运行没跑到这个节点
        want = requested.get(node_id)
        got = landed.get(node_id)
        entry: dict[str, Any] = {
            "node_id": node_id,
            "node_title": node.get("title") or node.get("type"),
            "requested_url": want,
            "landed_url": got,
        }
        if want and got:
            entry["redirected"] = not _same_page(want, got)
        elif want and not got:
            entry["redirected"] = None  # 节点没跑完，落地 URL 未知
        trace.append(entry)
    return trace


def build_navigation_verdict(trace: list[dict[str, Any]]) -> dict[str, Any] | None:
    """把重定向证据翻译成一句结论，省得每次都在提示词里重讲一遍该怎么读。"""
    redirected = [entry for entry in trace if entry.get("redirected") is True]
    if not redirected:
        return None
    first = redirected[0]
    return {
        "kind": "navigation_redirected",
        "message": (
            f"节点 `{first['node_id']}` 请求 {first['requested_url']}，"
            f"实际停在 {first['landed_url']}——导航没有到达目标页。"
            "后续节点都在错误的页面上找元素，改它们的 selector/delayMs 无效。"
        ),
        "repair_directions": [
            "目标页可直接访问 → 修正该节点的 targetUrl，或在登录完成后补一个 browser.open",
            "被路由守卫稳定重定向 → 改用菜单/按钮点击导航，selector 取自 inspect_page 的真实 DOM",
            "改完用一次运行的 navigation_trace 确认 landed_url 已是目标页",
        ],
    }


def _build_run_root_cause_hints(
    failed_node_id: str | None,
    all_logs: list[Any],
    failed_node_config: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """从运行日志里提取可验证根因，而不是只回显最后一个异常。"""
    hints: list[dict[str, Any]] = []
    if not failed_node_id:
        return hints

    login_detection_warnings = [
        log for log in all_logs
        if log.node_id and "check" in str(log.node_id).lower()
        and "继续执行" in str(log.message)
        and ("登录" in str(log.message) or "password" in str(log.detail).lower())
    ]
    failed_type = (failed_node_config or {}).get("type")
    if login_detection_warnings and failed_type in {"browser.wait", "browser.click", "browser.extract"}:
        hints.append({
            "type": "login_detection_may_have_skipped_login",
            "confidence": "high",
            "message": (
                "失败前出现登录检测节点超时但继续执行，随后浏览器节点失败。"
                "这通常不是当前 selector 单点失效，而是登录检测把“页面未渲染/登录页未出现”误判为“已登录”。"
            ),
            "next_actions": [
                "检查登录检测节点是否使用 continueOnError + countVariable 默认 0",
                "先用 inspect_page 或截图确认当前真实页面是登录页、应用页还是空白加载页",
                "默认保留 Cookies/localStorage 复用登录态；只有用户要求重置登录或确认过期 token 卡死时，才临时清理存储",
                "登录完成后显式 browser.open 到目标数据页，再等待目标表格",
                "修复前看状态块诊断里的 single_navigation_node、登录检测和筛选控件相关警告",
            ],
        })

    return hints


def _find_swallowed_critical_failures(
    all_logs: list[Any],
    failed_node_id: str | None,
    flow_id: str | None,
) -> list[dict[str, Any]]:
    """从运行日志中找出“失败但继续”的前置关键动作。

    不绑定页面或组件库，只看运行事实：关键筛选、提交、导航类动作失败后继续，
    后续等待/抽取节点再失败时，根因通常在前置链路。
    """
    if not failed_node_id or not flow_id:
        return []

    swallowed: list[dict[str, Any]] = []
    for log in all_logs:
        if log.level != "error" or not log.node_id or log.node_id == failed_node_id:
            continue
        message = str(log.message or "")
        if "继续执行" not in message and "continue" not in message.lower():
            continue
        marker = f"{message} {log.detail or ''}".lower()
        if not any(keyword in marker for keyword in (
            "筛选", "过滤", "查询", "搜索", "提交", "确认", "确定",
            "日期", "时间", "状态", "进度", "下拉", "多选",
            "filter", "search", "submit", "confirm", "date", "status",
        )):
            continue
        swallowed.append({
            "node_id": log.node_id,
            "message": message,
            "detail": log.detail,
        })
    return swallowed[-6:]


def _parse_runtime_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text[0] not in "[{\"0123456789-tnf":
        return value
    try:
        return json.loads(text)
    except Exception:
        return value


def _build_input_variable_defaults(input_variables: list[Any]) -> dict[str, Any]:
    """把流程声明的 input_variables 默认值转换成运行时变量。

    AI 工具的 run_flow 会直接调用 TaskManager；如果不在这里合并默认值，
    静态校验会认为变量已定义，但实际执行器的变量仓库里没有这些值。
    """
    defaults: dict[str, Any] = {}
    for variable in input_variables:
        if isinstance(variable, dict):
            name = variable.get("name")
            value = variable.get("value", variable.get("defaultValue", ""))
        else:
            name = getattr(variable, "name", None)
            value = getattr(variable, "value", "")
        if isinstance(name, str) and name.strip():
            defaults[name.strip()] = _parse_runtime_value(value)
    return defaults








# 需求里出现这些词，说明用户明确要的是按行/按列的结构化数据
_TABLE_REQUIREMENT_TOKENS = (
    "表格", "表头", "csv", "excel", "xlsx", "每行", "每一行", "逐行", "清单", "列表", "字段",
)
# 出现这些词，用户要的交付物是一篇文档
_DOCUMENT_REQUIREMENT_TOKENS = (
    "markdown", "md 格式", "总结", "摘要", "报告", "纪要", "综述", "文档", "文章",
)
_MIN_DOCUMENT_CHARS = 200  # 少于这个量的「总结」通常只有标题没正文
_DOCUMENT_FILE_SUFFIXES = (".md", ".markdown", ".txt", ".html", ".htm", ".docx", ".pdf")

# 正文压在压缩流/CID 编码里，按 UTF-8 读出来的是容器字节，不是人看到的字。
# 值是该格式的文件头：能验「这确实是一个 .pdf」，验不了「里面写了什么」。
_BINARY_DOCUMENT_FORMATS: dict[str, bytes] = {
    ".pdf": b"%PDF-",
    ".docx": b"PK\x03\x04",
    ".xlsx": b"PK\x03\x04",
    ".pptx": b"PK\x03\x04",
    ".doc": b"\xd0\xcf\x11\xe0",
    ".xls": b"\xd0\xcf\x11\xe0",
}
_MIN_BINARY_DOCUMENT_BYTES = 256  # 比最小的合法单页 PDF 还小，只兜空壳文件




def _looks_like_document_path(value: str) -> bool:
    return (
        "\n" not in value
        and len(value) < 400
        and value.lower().endswith(_DOCUMENT_FILE_SUFFIXES)
    )




_WHITESPACE_RUN = re.compile(r"\s+")
# 抓取值里取多长一段去正文里找。中文 8 字已经不可能撞车；再长会被脚本的换行/加粗切断。
_PROVENANCE_FRAGMENT = 8
_PROVENANCE_SAMPLE_VALUES = 40


def _collapse(text: str) -> str:
    return _WHITESPACE_RUN.sub(" ", text)




# 输入进、输出出，中间由模型写的加工节点。三条脚本通道与列变换节点都算，
# 少收一种类型就是同一个缺陷换个节点写就漏判
# 加工后仍有这个比例的体量，就当它什么也没做。留 5% 余量是给「去掉几行页眉」这类
# 真做了事但削减本来就小的加工；再宽就会把「一个字符没删」也算进正常范围
_TRANSFORM_EFFECT_RATIO = 0.95
# 短文本上下浮动几十字就能跨过比例线，判不准；这条判据只管大块文本
_TRANSFORM_MIN_CHARS = 2000
_TRANSFORM_PROBE_FRAGMENTS = 12
_TRANSFORM_PROBE_LEN = 24


def _as_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value or "")


def _text_survived(source: str, result: str) -> bool:
    """source 的内容是不是原封不动地还在 result 里。

    取等距片段而不是整串比对：加工节点通常会动几处空白或删掉几行，整串相等几乎不会成立，
    但那样的改动同样等于没清洗。片段全中说明连噪声段落都逐字保留。
    """
    haystack = _collapse(result)
    text = _collapse(source)
    step = max(len(text) // _TRANSFORM_PROBE_FRAGMENTS, _TRANSFORM_PROBE_LEN)
    probes = [
        text[offset : offset + _TRANSFORM_PROBE_LEN]
        for offset in range(0, len(text) - _TRANSFORM_PROBE_LEN, step)
    ]
    if not probes:
        return False
    return sum(probe in haystack for probe in probes) >= len(probes) - 1


def _find_ineffective_transforms(
    nodes: list[Any], variables: dict[str, Any]
) -> list[dict[str, Any]]:
    """加工节点的输出和它的输入几乎一模一样——跑通了，但什么也没加工。

    这是"清洗/去噪"类需求的主要失败形态：模型没看过真实数据，按想象写一套行级黑名单，
    而抽取节点交上来的往往是一大段连续文本，逐行规则一条都命不中。节点不报错、变量非空、
    产物照落，运行状态与结构审计全绿，只有把两个变量的体量摆在一起才看得出来。
    判据挂在「输出相对输入有没有变化」上而不是节点标题里的"清洗"二字：换个说法命名、
    换种脚本语言写，同样拦得住。
    """
    own_outputs: dict[str, set[str]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        outputs = {
            str(node.get(field))
            for field in ("outputVariable", "countVariable", "firstValueVariable", "variableName")
            if node.get(field)
        }
        if outputs:
            own_outputs[str(node.get("id", ""))] = outputs

    findings: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") not in TRANSFORM_NODE_TYPES:
            continue
        target = str(node.get("outputVariable") or "")
        if target not in variables:
            continue
        result = _as_text(variables[target])
        if len(result) < _TRANSFORM_MIN_CHARS:
            continue
        siblings = own_outputs.get(str(node.get("id", "")), set())
        for name, value in variables.items():
            # 同一个抽取节点的 outputVariable 与 firstValueVariable 装的是同一份内容，
            # 拿它俩互比必然"无变化"——只比别的节点交上来的输入
            if name == target or name in siblings or name in _RUNTIME_BUILTINS:
                continue
            source = _as_text(value)
            if len(source) < _TRANSFORM_MIN_CHARS:
                continue
            if len(result) < len(source) * _TRANSFORM_EFFECT_RATIO:
                continue
            if not _text_survived(source, result):
                continue
            findings.append({
                "issue": TRANSFORM_HAD_NO_EFFECT,
                "node_id": str(node.get("id", "?")),
                "message": (
                    f"加工节点 `{node.get('id', '?')}`（{node.get('type')}）的输出 `{target}` "
                    f"有 {len(result)} 字符，它的输入 `{name}` 有 {len(source)} 字符，"
                    f"体量只差 {max(0.0, (1 - len(result) / len(source))) * 100:.1f}%，"
                    "且输入的内容逐段原样出现在输出里：这个节点跑通了，但没有真的加工数据。"
                ),
                "fix": (
                    f"先 get_run_output 把 `{name}` 的实际内容读出来看清噪声长什么样，不要凭猜写规则。\n"
                    "若输入是整页文本（导航、页脚、内联样式混在一起、几乎不换行），"
                    "行级黑名单和按行去重必然一条都命不中——出路是回到抽取节点收窄 selector"
                    "（先 inspect_page 找只装目标内容的容器），不是继续加固脚本。\n"
                    "改完重跑，看新一次运行的 acceptance_audit 里这条是否消失；在体量真的变化之前，不要向用户汇报已完成清洗。"
                ),
                "output_variable": target,
                "input_variable": name,
            })
            break
    return findings






_SWEEP_NODE_TYPES = {"browser.paginateNext", "browser.clickLoadMore"}


def _find_incomplete_sweeps(nodes: list[Any], variables: dict[str, Any]) -> list[dict[str, Any]]:
    """翻页节点的输出与上游提取逐字相同 —— 一页都没翻动。

    这类残缺不报错：success、变量非空、行数正常，只是少了第 2 页往后的全部数据。
    两个列表相等是铁证，不花任何额外调用。
    """
    findings: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") not in _SWEEP_NODE_TYPES:
            continue
        swept = variables.get(node.get("outputVariable"))
        if not isinstance(swept, list) or not swept:
            continue
        for name, value in variables.items():
            if name == node.get("outputVariable") or not isinstance(value, list):
                continue
            if value and value == swept:
                findings.append({
                    "issue": "sweep_never_advanced",
                    "node_id": node.get("id"),
                    "message": (
                        f"节点 {node.get('id')}（{node.get('type')}）的输出 `{node.get('outputVariable')}` "
                        f"与 `{name}` 完全相同，共 {len(swept)} 条：说明翻页/加载更多一次也没生效，"
                        "只采到了第一页。"
                    ),
                    "fix": (
                        f"用 inspect_page 确认真实的分页控件，再修 selector `{node.get('selector')}`；"
                        "若该站是数字页码而非「下一页」按钮，把该节点改成 URL 翻页模式："
                        "填 urlTemplate（含 `${page}` 占位，必要时配 startPage/pageStep）并删掉 selector。"
                    ),
                })
                break
    return findings




def _coerce_table_rows(value: Any) -> list[Any]:
    if isinstance(value, list):
        if value and all(isinstance(item, str) for item in value):
            nested: list[Any] = []
            for item in value:
                parsed = _parse_runtime_value(item)
                if isinstance(parsed, (list, dict)):
                    nested.append(parsed)
            if nested:
                return nested
        return value if all(isinstance(item, (list, dict)) for item in value) else []
    return []


def _check_structured_rows(rows: list[Any]) -> dict[str, Any] | None:
    """契约条款之外的「行本身就是垃圾」。

    表头被当成数据行、半空行、整表被摊平成一个数组、分页按钮混进结果——这些在验收契约里
    没有对应条款也无法有：字段齐、行数够、日期合法，四项全过，交上来的还是一堆没法用的行。
    判据只看行的形状，不猜哪个变量是表头（那属于旧的启发式审计，已随它一起删掉）。
    """
    if not rows:
        return {"issue": "empty_rows", "message": "结果行为空。"}
    if all(isinstance(row, dict) for row in rows):
        mixed_issue = _detect_mixed_ui_rows(rows)
        if mixed_issue is not None:
            return mixed_issue
        header_issue = _detect_header_echo_rows(rows)
        if header_issue is not None:
            return header_issue
        sparse_issue = _detect_sparse_rows(rows)
        if sparse_issue is not None:
            return sparse_issue
        return _detect_single_column_text_shell(rows)
    if not all(isinstance(row, list) for row in rows):
        return {"issue": "unstructured_rows", "message": "结果不是 list[dict] 或 list[list]，无法稳定校验字段。"}
    lengths = [len(row) for row in rows if isinstance(row, list)]
    if not lengths:
        return {"issue": "unstructured_rows", "message": "结果没有可识别的行数组。"}
    mixed_issue = _detect_mixed_ui_rows(rows)
    if mixed_issue is not None:
        return mixed_issue
    if len(rows) == 1 and lengths[0] > 30:
        return {
            "issue": "whole_table_flattened",
            "message": "结果只有 1 行但包含大量单元格，疑似整张表被抽成一个扁平数组。",
        }
    return None


def _detect_header_echo_rows(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """检测表头被当成数据行抓了进来（每个字段的值等于自己的列名）。

    为固定表头/虚拟滚动而把表头和表体渲染成两个独立 <table> 是组件库表格的通行做法，
    table 模式逐个抓就会多出这样一行。判定只看「值 == 列名」这个特征，不依赖具体框架。
    """
    echo_indexes = [
        index for index, row in enumerate(rows[:80])
        if _is_header_echo_row(row)
    ]
    if not echo_indexes:
        return None
    return {
        "issue": "header_row_as_data",
        "message": (
            f"第 {', '.join(str(i) for i in echo_indexes[:5])} 行的值与列名完全相同，"
            "说明表头行被当成了数据行抓取。常见于表头/表体分离渲染的表格组件，"
            "需要把抽取范围限定到表体：标准表格用 tbody tr，组件库表格从 inspect_page 的 "
            "tables[].row_selector 或 page_layout[].html 里取该站点真实的表体 class，不要套用其他站点的类名。"
        ),
        "header_echo_row_indexes": echo_indexes[:10],
    }


def _is_header_echo_row(row: dict[str, Any]) -> bool:
    filled = [(str(k).strip(), str(v).strip()) for k, v in row.items() if str(v).strip()]
    if len(filled) < 2:
        return False
    return all(key == value for key, value in filled)


_UI_CONTROL_TOKENS = frozenset({"上一页", "下一页", "确定", "取消", "今天", "清空"})
_UI_CONTROL_CELL_MAX_LEN = 8  # 控件文案都很短，超过这个长度的单元格按正文看待
_MIXED_UI_ROWS_TOLERANCE = 0.05  # 噪声行占比低于此值只提示，不判整次抓取不合格
_MIXED_UI_ROWS_TOLERATED_MAX = 3

_SPARSE_ROW_FILL_RATIO = 0.5  # 有效字段占比低于此值视为空行
_SPARSE_ROWS_SHARE = 0.3  # 稀疏行占比超过此值才报，少量合计/备注行属正常


def _detect_sparse_rows(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """检测大量"只有一两个字段有值"的近空行。

    selector 圈得过宽时，按钮列、操作列、渲染占位都会各自变成一行。
    """
    sparse_indexes: list[int] = []
    for index, row in enumerate(rows):
        if not row:
            continue
        filled = sum(1 for value in row.values() if str(value).strip())
        if filled / len(row) < _SPARSE_ROW_FILL_RATIO:
            sparse_indexes.append(index)
    if not sparse_indexes or len(sparse_indexes) / len(rows) <= _SPARSE_ROWS_SHARE:
        return None
    return {
        "issue": "sparse_rows",
        "message": (
            f"{len(sparse_indexes)}/{len(rows)} 行的有效字段不足一半，基本是空行。"
            "通常是抽取范围里混进了操作列按钮、合计行或渲染占位，"
            "真实业务行数远少于 row_count，不能据此认为抓取达标。"
        ),
        "sparse_row_indexes": sparse_indexes[:10],
    }


SINGLE_COLUMN_TEXT_SHELL = "single_column_text_shell"

_SHELL_PAYLOAD_CHAR_SHARE = 0.8  # 一列独占的字符占比，超过它其余列就没装下多少页面信息
_SHELL_PAYLOAD_MIN_AVG_LEN = 40  # 载荷列的平均长度：短到这个数以下更像正常的窄表
_SHELL_CONSTANT_MAX_DISTINCT = 3  # 取值不超过这么多种，且都很短 → 是作者自己写的标签，不是页面字段
_SHELL_CONSTANT_MAX_LEN = 12
_SHELL_MIN_ROWS = 5


def _is_enumeration_column(values: list[Any]) -> bool:
    """整列是 1、2、3…这样的自增序号——脚本生成的，页面上没有这个字段。"""
    numbers: list[int] = []
    for value in values:
        text = str(value).strip()
        if not re.fullmatch(r"\d{1,6}", text):
            return False
        numbers.append(int(text))
    return len(set(numbers)) == len(numbers) and numbers == sorted(numbers)


def _is_authored_label_column(values: list[Any]) -> bool:
    """整列只在少数几个短标签之间取值，例如「主题标题 / 主题正文 / 回复」。"""
    distinct = {str(value).strip() for value in values if str(value).strip()}
    if not distinct or len(distinct) > _SHELL_CONSTANT_MAX_DISTINCT:
        return False
    return all(len(value) <= _SHELL_CONSTANT_MAX_LEN for value in distinct)


def _detect_single_column_text_shell(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """检测「一列装整段文本、其余列是自己编的」的假表格。

    是 no_table_like_output 被绕开的标准姿势：被要求交结构化数据时，不回抽取节点拆字段，
    而是把整页文本切成段塞进 `内容` 列，再补一个序号列和一个类型列凑成 list[dict]。
    行数、字段数、非空率全部达标，只有「页面上真实存在的字段一个都没拆出来」这一点不达标，
    所以判据只看列本身的信息量：序号是脚本生成的，少数几个短标签是作者写的，两者都不来自页面。
    """
    if len(rows) < _SHELL_MIN_ROWS:
        return None
    columns = {key for row in rows for key in row}
    if len(columns) < 2:
        return None
    per_column = {key: [row.get(key, "") for row in rows] for key in columns}
    lengths = {key: sum(len(str(value)) for value in values) for key, values in per_column.items()}
    total = sum(lengths.values())
    if total <= 0:
        return None
    payload = max(lengths, key=lambda key: lengths[key])
    if lengths[payload] / total < _SHELL_PAYLOAD_CHAR_SHARE:
        return None
    if lengths[payload] / len(rows) < _SHELL_PAYLOAD_MIN_AVG_LEN:
        return None
    others = [key for key in columns if key != payload]
    if not all(
        _is_enumeration_column(per_column[key]) or _is_authored_label_column(per_column[key])
        for key in others
    ):
        return None
    return {
        "issue": SINGLE_COLUMN_TEXT_SHELL,
        "message": (
            f"`{payload}` 一列占了全部字符的 {lengths[payload] / total:.0%}，"
            f"其余 {len(others)} 列（{'、'.join(sorted(map(str, others)))}）不是自增序号就是"
            "只在少数几个短标签间取值——这两种列都不是从页面上拆出来的字段。"
            "这不是一张表格，是把整段文本切开后套了个表格壳：行数和字段数都达标，"
            "但用户拿到的仍然是原来那团文本。"
        ),
        "payload_column": str(payload),
        "payload_char_share": round(lengths[payload] / total, 3),
    }


def _is_ui_control_row(compact: list[str]) -> bool:
    """整行是否是日历面板 / 分页 / 按钮这类控件行。

    控件关键词只在短单元格上全等匹配。之前是把整行拼起来做子串扫描，
    结果「不确定」命中「确定」，一条 500 字的论坛评论被判成分页控件——
    助手据此反复收窄一个本来就正确的 selector，直到撞上 failure budget。
    """
    weekday_set = {"日", "一", "二", "三", "四", "五", "六"}
    if len(compact) == 7 and set(compact) <= weekday_set:
        return True
    numeric_cells = sum(1 for value in compact if re.fullmatch(r"\d{1,2}", value))
    if len(compact) in {6, 7} and numeric_cells >= 5:
        return True
    return any(
        len(value) <= _UI_CONTROL_CELL_MAX_LEN and value in _UI_CONTROL_TOKENS
        for value in compact
    )


def _detect_mixed_ui_rows(rows: list[Any]) -> dict[str, Any] | None:
    """检测抓取结果是否混入日期面板、分页、按钮等非业务 UI 行。"""
    ui_like: list[tuple[int, str]] = []
    scanned = rows[:80]
    for index, row in enumerate(scanned):
        values: list[str]
        if isinstance(row, dict):
            values = [str(value).strip() for value in row.values()]
        elif isinstance(row, list):
            values = [str(value).strip() for value in row]
        else:
            continue
        compact = [value for value in values if value]
        if compact and _is_ui_control_row(compact):
            ui_like.append((index, "|".join(compact)[:120]))
    if not ui_like:
        return None
    # 页首导航这类零星噪声不该推翻整次抓取：降级为提示，由 AI 拿着样本自行判断是否值得收窄
    tolerated = (
        len(ui_like) <= _MIXED_UI_ROWS_TOLERATED_MAX
        and len(ui_like) / len(scanned) <= _MIXED_UI_ROWS_TOLERANCE
    )
    return {
        "issue": "mixed_ui_rows",
        "severity": "warning" if tolerated else "blocking",
        "message": (
            f"{len(ui_like)}/{len(scanned)} 行疑似日历、分页或按钮等非业务 UI 控件行，占比很低。"
            "样本确实是噪声就收窄 selector 后重跑；只是正文碰巧短小则可以照常交付，无需为此重改流程。"
            if tolerated else
            "结果中混入了日历、分页、按钮或其他非业务 UI 控件行。"
            "这说明抽取 selector/scope 过宽，虽然有行数据，但不能证明业务筛选和字段约束可信。"
        ),
        "ui_like_row_indexes": [index for index, _ in ui_like[:10]],
        # 只给行号，模型无从判断是真噪声还是误报，只能盲改 selector
        "ui_like_row_samples": [{"row_index": index, "text": text} for index, text in ui_like[:5]],
    }


def _row_get(row: Any, headers: list[str] | None, field: str) -> Any:
    if isinstance(row, dict):
        if field in row:
            return row[field]
        normalized = field.strip().lower()
        for key, value in row.items():
            if str(key).strip().lower() == normalized:
                return value
        return None
    if isinstance(row, list) and headers:
        for index, header in enumerate(headers):
            if str(header).strip() == field and index < len(row):
                return row[index]
    return None










_REQUIREMENT_ACTION_PREFIXES = ("抓取", "采集", "提取", "获取", "爬取", "导出", "下载", "统计", "收集")
_REQUIREMENT_GENERIC_SUFFIXES = ("数据", "信息", "内容", "结果", "模块", "页面", "列表", "表格", "报表", "明细")
# 去掉修饰后只剩这些词说明没指向任何具体业务对象，拿去比对只会制造噪音
_REQUIREMENT_STOP_TERMS = frozenset({
    "数据", "信息", "内容", "结果", "模块", "页面", "列表", "表格", "报表", "明细",
    "全部", "所有", "登录", "变量", "流程", "网站", "系统",
})






_CJK_IDEOGRAPHS = re.compile("[\\u4e00-\\u9fff]")  # CJK 统一汉字区块 U+4E00..U+9FFF


def _shares_substring(term: str, haystack: str) -> bool:
    """目标词与输出是否有足够长的连续重合。

    整词匹配太脆：「所有订单」对不上表头「订单号」。中文两字已具区分度，
    拉丁字母两字符（in/id/re）满地都是，要三字符起。
    """
    window = 2 if _CJK_IDEOGRAPHS.search(term) else 3
    return any(
        term[i : i + window] in haystack
        for i in range(len(term) - window + 1)
    )




def _build_quality_repair_plan(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把验收审计的 issue 翻译成「该动哪个节点」的可执行步骤。

    审计只说交付物哪里不合格。模型据此仍要自己猜动哪个节点，而猜错的代价是再跑一次完整
    运行（常以分钟计）。这里只补这一层：issue 名 → 该动的节点 + 判定改对了的依据。
    lint 已经随 finding 一起给出 fix 的问题不在这里重复一遍：同一条建议出现两处，
    改了一处就会长期不一致。
    """
    plan: list[dict[str, Any]] = []
    issue_names = {str(issue.get("issue", "")) for issue in issues}

    # 证据本身不成立：流程可能完全没坏，是这份产物不能代表当前定义，出路只有重跑。
    if issue_names & {"stale_run_evidence", "definition_digest_mismatch", "orphaned_run_evidence"}:
        plan.append({
            "action": "rerun_current_revision",
            "reason": "这份运行产物不对应当前流程定义，不能作为验收依据。",
            "steps": [
                "不要改流程结构：本条不是流程缺陷，改一次只会让证据再作废一次。",
                "直接对当前 flow_id 重新调用 run_flow，按新任务返回的 acceptance_audit 汇报。",
            ],
        })
    if "acceptance_contract_missing" in issue_names:
        plan.append({
            "action": "freeze_acceptance_contract",
            "reason": "这次运行没有携带交付验收契约，审计无从判断该验哪个变量、哪些业务条件。",
            "steps": [
                "调用 set_acceptance_contract：requirements 逐条记用户原话，deliverables 逐个声明交付变量与后置条件。",
                "契约齐了再重新运行；不要为了让审计通过而少写条款。",
            ],
        })
    if "whole_table_flattened" in issue_names:
        plan.append({
            "action": "fix_table_extraction_selector",
            "reason": "整张表被抽成了一个扁平数组，通常是 extract selector 指向表格容器而不是数据行。",
            "steps": [
                "在状态块的节点列表里找到 browser.extract 表格节点。",
                "将 selector 改为真实数据行选择器；标准表格优先 tbody tr，Element UI/Ant Design 从 inspect_page/page_layout 中找行 class。",
                "保持 extractMode='table'，补充 outputVariable 和 countVariable。",
                "重跑后看 acceptance_audit：行数应当与页面上的记录条数同量级，而不是 1 行。",
            ],
        })
    if issue_names & {"deliverable_not_table", "unstructured_rows", "rows_not_objects"}:
        plan.append({
            "action": "produce_structured_rows",
            "reason": "交付变量不是按行结构化的数据，字段级验收无从进行。",
            "steps": [
                "先 get_run_output 看这个变量的实际形态：是纯文本、单层字符串数组，还是 list[list]。",
                "改抽取节点 extractMode='table' 让它直接产出 list[dict]，"
                "不要再加一个脚本节点把文本二次拼成表——那只是把同一个问题往后挪一格。",
                "契约要求具名字段时，字段名必须来自同一行的结构，不能把表头和行分开猜测拼接。",
                "若这次交付物本来就该是一篇文档而不是表格，直接向用户确认交付形态，"
                "不要造一个 rows 变量来迎合契约。",
            ],
        })
    if SINGLE_COLUMN_TEXT_SHELL in issue_names:
        plan.append({
            "action": "split_real_page_fields",
            "reason": "输出只是把整段文本切开塞进一列，再补上序号和类型凑成表格，页面上的字段一个都没拆出来。",
            "steps": [
                "**禁止继续在脚本里切文本**：换分隔符、换切分粒度都只会得到另一种切法的同一团文本。",
                "调用 inspect_page 看目标区域的 page_layout，确认页面上一条记录由哪几个元素组成"
                "（如标题、作者、时间、正文各自的 class）。",
                "把 browser.extract 改成按记录抽取：一条记录一行，每个字段一列；"
                "字段各自有 selector 时用多个抽取节点，整块结构规整时用 extractMode='table'。",
                "字段确实无法从页面拆出来时，如实告诉用户这个站点只能拿到整段文本，让用户决定，不要用序号列凑数。",
            ],
        })
    if "required_fields_missing" in issue_names:
        plan.append({
            "action": "extract_the_missing_fields",
            "reason": "契约点名的字段在数据行里不存在，抽取节点没把这一列拆出来。",
            "steps": [
                "先 get_run_output 看行里实际有哪些键：是字段名对不上（列名被改写），还是这一列压根没抽。",
                "列名对不上就改抽取节点的字段命名，让它与契约里的 required_fields 逐字一致。",
                "整列缺失时调用 inspect_page 确认页面上有没有这个字段；有就补进抽取范围，"
                "确实没有就如实告诉用户这个站点拿不到该字段，不要造一列空值。",
            ],
        })
    if "header_row_as_data" in issue_names:
        plan.append({
            "action": "filter_header_rows",
            "reason": "表头行被当成数据行抓了进来（每个字段的值等于自己的列名）。",
            "steps": [
                "将 selector 改为 tbody tr；若已是 tbody tr 仍出现表头，改用 extractMode='table'（自动过滤表头）。",
                "表头/表体分离渲染的组件库表格，从 inspect_page 的 tables[].row_selector 取该站点真实的表体 class，不要套用其他站点的类名。",
            ],
        })
    if "allowed_values_violation" in issue_names:
        plan.append({
            "action": "make_the_filter_actually_requery",
            "reason": "字段出现了契约允许范围外的值，多数是下拉筛选选中了但没重新拉数据。",
            "steps": [
                "issue.message 已列出实际非法值，直接据此判断是筛选没生效还是抓的列不对（无需再调 get_run_output）。",
                "检查下拉筛选链路：点击选项之后必须点查询/确认按钮，否则前端只是选中了选项，页面数据没变。",
                "查询按钮点击后补 browser.wait 等待表格更新（waitForSelector 或 delayMs 2000），再执行 extract。",
                "非法值恰好等于列名时属于表头混入，按 filter_header_rows 处理。",
            ],
        })
    if "date_range_violation" in issue_names:
        plan.append({
            "action": "verify_date_filter_applied",
            "reason": "输出数据包含日期范围外的记录，说明筛选条件填入 UI 但未真实生效（组件内部状态未更新）。",
            "steps": [
                "**禁止只改日期 selector 后再跑一遍**——这只会重复得到未生效筛选的结果。",
                "在状态块的节点列表里找到日期筛选相关节点；如果存在 browser.fill 日期输入框，或日期 click 节点 selector 过宽，必须重建日期链路。",
                "调用 inspect_page(scope_selector=筛选区域容器)。**若返回 date_controls[].interaction_recipe，按 steps 重建节点（selector 直接用，日期文本和节点数量按本次任务改写）；若 date_controls 为空**，按同样思路自己搭：写日期 → 提交 → 回读 → 校验。",
                "优先键入日期文本（browser.fill fillMode='type' + browser.press Enter），它与运行当天无关；点日历格要求面板正好停在目标月份，且翻月次数绝不能写死。",
                "点日历格时单元格 selector 必须排除上/下月的单元格（Element UI 的 prev-month/next-month、Ant Design 的非 cell-in-view）。",
                "在查询按钮点击后增加 browser.wait 等待表格数据更新（waitForSelector 或 delayMs 2000），再执行 extract。",
                "回读校验（抽取输入框 value + 脚本比对）是这条的硬门控：没有它，下一次运行同样无法自证筛选生效。",
            ],
        })
    if issue_names & {"mixed_ui_rows", "sparse_rows"}:
        plan.append({
            "action": "narrow_extraction_scope",
            "reason": "输出中混入日期面板、分页、按钮等非业务行，或大量近空行，说明抽取范围过宽。",
            "steps": [
                "调用 inspect_page 查看业务数据区域与浮层/筛选控件的 DOM 边界。",
                "将抽取 selector 或 scope 收窄到业务数据容器内的真实数据项/数据行。",
                "避免使用 tbody tr、[role=row] 等全页面宽泛 selector，除非已经限定父容器。",
                "重跑后看 acceptance_audit：行数会随噪声行被剔除而下降，这是修对了而不是抓少了。",
            ],
        })
    if "sweep_never_advanced" in issue_names:
        plan.append({
            "action": "fix_pagination_trigger",
            "reason": "翻页节点的输出与翻页前的提取逐字相同，只采到了第一页。",
            "steps": [
                "先 inspect_page 读真实的分页控件，再改 selector——不要凭常见类名猜「下一页」。",
                "若该站是数字页码而不是「下一页」按钮，点击式翻页走不通："
                "改成按 URL 遍历（?p=N）放进循环，比点按钮稳且不依赖任何按钮 selector。",
                "重跑后看节点 detail 里的 `N 页 · [每页条数] · stop=…`，确认页数大于 1 再谈验收。",
            ],
        })
    if issue_names & {"empty_rows", "too_few_rows", "coverage_ratio_violation"}:
        plan.append({
            "action": "collect_all_the_rows",
            "reason": "运行成功但行数不够，数据没抓全。",
            "steps": [
                "先看抽取节点前有没有等待：表格异步渲染时 extract 会在空表上成功返回 0 行。",
                "确认翻页是否真的翻完：循环存在时看 countVariable 的实际值，只有第 1 页按 fix_pagination_trigger 处理。",
                "页面本身就只有这些数据时，不要改契约来迁就——如实告诉用户实际条数与页面声明总数的差距，让用户判断。",
            ],
        })
    if TRANSFORM_HAD_NO_EFFECT in issue_names:
        plan.append({
            "action": "make_transform_actually_transform",
            "reason": "加工节点跑通了，但输出与输入几乎逐字相同，用户要的清洗/加工一步都没发生。",
            "steps": [
                "先 get_run_output 读输入变量的真实内容，确认噪声的实际形态（是独立成行，还是和正文连在一段里）。",
                "噪声不成行时，加固脚本无解：回到抽取节点，用 inspect_page 找只装目标内容的容器并收窄 selector。",
                "重跑后看 acceptance_audit 里这条是否消失——体量没变就是没做成，不要改口径汇报成功。",
            ],
        })
    if issue_names & {"document_missing_source_data", "source_variable_missing"}:
        plan.append({
            "action": "wire_document_to_extracted_data",
            "reason": "文档写出来了，但正文里没有本次抓取到的原文，生成节点用的不是抽取节点的输出。",
            "steps": [
                "用 get_run_output 看契约里 sourceVariables 点名的变量：先确认它非空、装的是正文而不是标题或计数。",
                "读生成节点的代码：它读的变量名是否就是那个来源变量，写文件时写进去的是不是这个变量的内容。",
                "把需求原话写成文档标题对这条判据无效——它比的是本次抓取值，不是需求关键词。",
            ],
        })
    if issue_names & {"document_too_short", "document_unreadable", "document_binary_too_small", "document_binary_header_mismatch"}:
        plan.append({
            "action": "fix_document_output",
            "reason": "文档型交付物没有正常写出：正文为空、读不出来，或文件是个打不开的空壳。",
            "steps": [
                "先确认交付变量存的是什么：正文文本，还是写文件节点返回的路径——契约按哪一种声明就按哪一种产出。",
                "写文件节点必须真的把内容写进去并 flush/close；只创建文件不落内容会得到一个几百字节的空壳。",
                "正文确实偏短时回到抽取节点：用 inspect_page 确认正文容器 selector 抓的是整篇内容，而非单个标题元素；"
                "分页/展开类内容检查循环是否翻完（看 countVariable 实际值）。",
            ],
        })
    if issue_names & {"file_missing", "file_too_small", "file_extension_mismatch", "file_path_invalid", "file_path_outside_workspace", "file_unreadable"}:
        plan.append({
            "action": "fix_file_deliverable",
            "reason": "文件型交付物的路径、扩展名或体积不符合契约声明。",
            "steps": [
                "让写文件节点把最终路径写回契约点名的交付变量：变量里存别的东西（内容、目录、None）都会判不通过。",
                "路径必须落在 RPA 工作区内，扩展名与契约声明一致；改扩展名之前先确认写出的确实是那个格式。",
                "文件过小时按 fix_document_output 检查内容有没有真的写进去。",
            ],
        })
    if not plan and issues:
        plan.append({
            "action": "inspect_and_repair_flow_structure",
            "reason": "验收审计不通过，但没有匹配到专门修复模板。",
            "steps": [
                "对照状态块的节点列表与诊断段查看结构风险。",
                "调用 get_run_output 查看实际变量形态。",
                "根据 issues 修复最靠前的结构性问题后重新运行。",
            ],
        })
    return plan

