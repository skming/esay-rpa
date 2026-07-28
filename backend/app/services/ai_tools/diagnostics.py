"""运行结果诊断：失败根因推断、输出数据质量断言、修复方案生成。

面向"流程跑完之后"的分析，与面向"跑之前"的 lint 互补。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.services.ai_tools.variables import _RUNTIME_BUILTINS

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

# 交付内容对不上需求：表格与文档两条审计路径各报各的名字。
OUTPUT_CONTENT_MISMATCH = "output_content_may_not_match_requirement"
DOCUMENT_CONTENT_MISMATCH = "document_content_may_not_match_requirement"

# 报出其中任意一条，模型的 content_match_confirmed 才解锁（编排层消费）。漏掉一条，
# 那条路径上的确认位就永远解不开：照 fix 传 true 会被剥掉，拿回一模一样的失败，
# 两次即触发质量熔断，流程锁死在一个它无论如何都满足不了的判据上。
CONTENT_MISMATCH_ISSUES = frozenset({OUTPUT_CONTENT_MISMATCH, DOCUMENT_CONTENT_MISMATCH})

# 文档正文与本次抓取数据无交集：与上面两条不同，这条不接受 content_match_confirmed 自证，
# 因为它比的就是「模型自己写的字」之外的证据。
DOCUMENT_MISSING_RUN_DATA = "document_missing_run_data"


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
                "修复前调用 lint_flow 查看 single_navigation_node、登录检测和筛选控件相关警告",
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




def _find_header_variable(variables: dict[str, Any]) -> list[str] | None:
    for name, value in variables.items():
        lower = name.lower()
        if "header" not in lower and "columns" not in lower:
            continue
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return [str(item) for item in value]
    return None


def _find_table_candidates(variables: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for name, value in variables.items():
        rows = _coerce_table_rows(value)
        if not rows:
            continue
        score = 0
        lower = name.lower()
        if any(keyword in lower for keyword in ("row", "rows", "table", "data", "list")):
            score += 10
        if "header" in lower or "count" in lower:
            score -= 8
        score += min(len(rows), 20)
        candidates.append({"name": name, "rows": rows, "score": score})
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates


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


def _requirement_wants_document(requirement_text: str) -> bool:
    """需求要的是一篇文档，而不是一张按行结构化的表。

    形态判错的代价是单向的：把文档需求按表格审，助手只能临时造一个 rows 变量
    去喂审计，用户真正要的那篇文档反而没人验。所以需求里同时出现表格类词就
    退回按表格审——宁可漏判，不可误判。
    """
    text = (requirement_text or "").lower()
    if any(token in text for token in _TABLE_REQUIREMENT_TOKENS):
        return False
    return any(token in text for token in _DOCUMENT_REQUIREMENT_TOKENS)


def _looks_like_document_path(value: str) -> bool:
    return (
        "\n" not in value
        and len(value) < 400
        and value.lower().endswith(_DOCUMENT_FILE_SUFFIXES)
    )


def _find_document_output(variables: dict[str, Any]) -> dict[str, Any] | None:
    """挑出这次运行最像交付文档的输出：写出去的文件路径，或够长的正文文本。"""
    best: dict[str, Any] | None = None
    for name, value in variables.items():
        if not isinstance(value, str):
            continue
        if _looks_like_document_path(value):
            # 文件路径优先于正文变量：正文往往是中间量，落盘的那份才是交付物
            candidate = {"name": name, "kind": "file", "value": value, "score": 1000 + len(name)}
        elif len(value) >= _MIN_DOCUMENT_CHARS:
            candidate = {"name": name, "kind": "text", "value": value, "score": min(len(value), 20_000)}
        else:
            continue
        if best is None or candidate["score"] > best["score"]:
            best = candidate
    return best


_WHITESPACE_RUN = re.compile(r"\s+")
# 抓取值里取多长一段去正文里找。中文 8 字已经不可能撞车；再长会被脚本的换行/加粗切断。
_PROVENANCE_FRAGMENT = 8
_PROVENANCE_SAMPLE_VALUES = 40


def _collapse(text: str) -> str:
    return _WHITESPACE_RUN.sub(" ", text)


def _run_data_fragments(variables: dict[str, Any], document_name: str) -> list[str]:
    """从本次运行抓到的变量里取若干原文片段，用来验证文档正文确实装了这些数据。

    排除交付物变量自己和运行时内置量（路径、时间戳）：拿产物路径去产物正文里找，
    找到的只是脚本把路径写进了页脚。
    """
    fragments: list[str] = []
    for name, value in variables.items():
        if name == document_name or name in _RUNTIME_BUILTINS:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict):
                item = " ".join(str(v) for v in item.values())
            text = _collapse(str(item)).strip()
            if len(text) < _PROVENANCE_FRAGMENT or text.startswith(("http://", "https://", "/")):
                continue
            fragments.append(text[:_PROVENANCE_FRAGMENT])
            middle = len(text) // 2
            if len(text) >= _PROVENANCE_FRAGMENT * 3:
                fragments.append(text[middle : middle + _PROVENANCE_FRAGMENT])
            if len(fragments) >= _PROVENANCE_SAMPLE_VALUES * 2:
                return fragments
    return fragments


def _audit_document_provenance(
    document: dict[str, Any], body: str, variables: dict[str, Any]
) -> dict[str, Any] | None:
    """文档正文里是否留下了本次运行抓到的数据。

    需求关键词比对挡不住这一类：文档正文整篇由脚本写出，把需求原话写成标题
    （「# 帖子内容总结」「## 生成总结」）就能让关键词判据通过，而这比修抽取节点便宜得多——
    模型上一轮已经明说要这么干。所以文档必须拿抓取值来验，不能拿它自己写的字自证。
    平台没有语义改写类节点，脚本只能搬运原文，因此逐字找不到就是真没搬进去。
    """
    fragments = _run_data_fragments(variables, str(document.get("name") or ""))
    if not fragments:
        return None
    haystack = _collapse(body)
    if any(fragment in haystack for fragment in fragments):
        return None
    return {
        "issue": DOCUMENT_MISSING_RUN_DATA,
        "message": (
            f"文档 `{document['name']}` 里找不到本次运行抓到的任何一段原文"
            f"（比对了 {len(fragments)} 个片段，例如 {fragments[:3]}）。"
            "正文是生成节点自己写的固定文案，抓来的数据没有进到交付物里。"
        ),
        "fix": (
            "检查生成节点：确认它读的是抽取节点的输出变量，且写文件时用的是这个变量的内容，"
            "不是脚本里的示例文本或标题模板。"
        ),
    }


def _audit_binary_document(document: dict[str, Any], path: Path) -> list[dict[str, Any]]:
    """二进制产物只验「确实是这个格式、不是空壳」，正文一律不验。

    把 PDF/Office 文件按 UTF-8 读出来的是容器字节，需求关键词逐字比对必然落空：一份内容
    完全正确的 PDF 会被判成内容不符，助手照 fix 传 content_match_confirmed 也救不回来，
    同一条失败两次就触发质量熔断，流程锁死在一个它无论如何都满足不了的判据上。
    验不了就明说验不了——用一个必然失败的判据冒充审计，比不审更坏。
    """
    header = _BINARY_DOCUMENT_FORMATS[path.suffix.lower()]
    size = path.stat().st_size
    if size < _MIN_BINARY_DOCUMENT_BYTES:
        return [{
            "issue": "document_binary_too_small",
            "message": f"产物 `{document['name']}` 只有 {size} 字节，装不下一篇内容，多半是写文件节点只落了个空壳。",
            "fix": "检查生成节点：确认正文变量非空、写入过程没有异常被吞掉，再重跑。",
        }]
    with path.open("rb") as handle:
        magic = handle.read(len(header))
    if magic != header:
        return [{
            "issue": "document_binary_header_mismatch",
            "message": (
                f"产物 `{document['name']}` 扩展名是 {path.suffix.lower()}，但文件头不是 {header!r}，"
                "多数查看器会直接打不开。"
            ),
            "fix": "检查生成节点是否按该格式的规范写入（自己拼字节流尤其容易漏文件头），或改用成熟的库生成。",
        }]
    return [{
        "severity": "warning",
        "issue": "document_content_not_text_verifiable",
        "message": (
            f"产物 `{document['name']}` 是 {path.suffix.lower()} 二进制文档（{size} 字节），"
            "本工具读不到它的正文，已跳过与需求的关键词比对——这不代表内容已核对。"
        ),
        "fix": (
            "核对喂给该文档的源变量（正文/摘要变量）与需求是否一致；"
            "内容是否正确请交给用户过目，不要在没看过正文的情况下宣称验收通过。"
        ),
    }]


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
                        "若该站是数字页码而非「下一页」按钮，改用按 URL 翻页（?p=N）而不是点击翻页。"
                    ),
                })
                break
    return findings


def _describe_output_variables(variables: dict[str, Any]) -> list[dict[str, Any]]:
    """如实列出这次运行到底产出了什么，以及每个变量为什么不算表格。

    只说「没有表格型变量」而不说有什么，模型除了换个写法再试一次别无选择，
    重复调用拿回的还是同一句话——空转就是这么烧出来的。
    """
    described: list[dict[str, Any]] = []
    for name, value in variables.items():
        item: dict[str, Any] = {"name": name}
        if isinstance(value, list):
            item["kind"] = f"list（{len(value)} 项）"
            item["sample"] = [str(v)[:60] for v in value[:2]]
            if value and not _coerce_table_rows(value):
                item["why_not_table"] = "元素是纯文本，不是 dict/list，无法按行结构化"
        elif isinstance(value, dict):
            item["kind"] = f"dict（{len(value)} 键）"
            item["sample"] = list(value)[:8]
            item["why_not_table"] = "单个对象不是行集合；要成表需要 list[dict]"
        else:
            text = str(value)
            item["kind"] = f"text（{len(text)} 字符）"
            item["sample"] = text[:80]
            item["why_not_table"] = "标量文本不是行集合"
        described.append(item)
    return described[:20]


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


def _check_structured_rows(rows: list[Any], headers: list[str] | None) -> dict[str, Any] | None:
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
        return None
    if not all(isinstance(row, list) for row in rows):
        return {"issue": "unstructured_rows", "message": "结果不是 list[dict] 或 list[list]，无法稳定校验字段。"}
    lengths = [len(row) for row in rows if isinstance(row, list)]
    if not lengths:
        return {"issue": "unstructured_rows", "message": "结果没有可识别的行数组。"}
    mixed_issue = _detect_mixed_ui_rows(rows)
    if mixed_issue is not None:
        return mixed_issue
    if headers:
        mismatched = [
            {"row_index": index, "column_count": length}
            for index, length in enumerate(lengths)
            if length != len(headers)
        ]
        if mismatched:
            return {
                "issue": "header_row_length_mismatch",
                "message": (
                    f"表头列数为 {len(headers)}，但存在 {len(mismatched)} 行数据列数不一致。"
                    "这会导致 Excel 表头和值错位或只写出部分列。"
                ),
                "headers_count": len(headers),
                "sample_mismatched_rows": mismatched[:5],
            }
        if max(lengths) > len(headers) * 2:
            return {
                "issue": "whole_table_flattened",
                "message": (
                    f"检测到单行列数 {max(lengths)} 远大于表头列数 {len(headers)}，"
                    "疑似把整张表抽成一个扁平文本数组，而不是逐行抽取。"
                ),
            }
        return None
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


def _assert_date_range(
    rows: list[Any],
    headers: list[str] | None,
    field: str,
    start_date: str | None,
    end_date: str | None,
) -> list[dict[str, Any]]:
    from datetime import date

    issues: list[dict[str, Any]] = []
    start = date.fromisoformat(start_date) if start_date else None
    end = date.fromisoformat(end_date) if end_date else None
    bad: list[dict[str, Any]] = []
    missing = 0
    for index, row in enumerate(rows):
        raw = _row_get(row, headers, field)
        if raw is None:
            missing += 1
            continue
        match = re.search(r"\d{4}-\d{2}-\d{2}", str(raw))
        if not match:
            bad.append({"row": index + 1, "value": raw, "reason": "无法解析日期"})
            continue
        current = date.fromisoformat(match.group(0))
        if (start and current < start) or (end and current > end):
            bad.append({"row": index + 1, "value": raw, "reason": "日期超出范围"})
    if missing == len(rows):
        issues.append({"issue": "date_field_missing", "message": f"所有行都找不到日期字段 `{field}`。"})
    elif bad:
        issues.append({
            "issue": "date_range_violation",
            "message": f"字段 `{field}` 存在 {len(bad)} 行不在日期范围内。",
            "examples": bad[:5],
        })
    return issues


def _assert_allowed_values(
    rows: list[Any],
    headers: list[str] | None,
    field: str,
    allowed_values: list[str],
) -> list[dict[str, Any]]:
    allowed = {str(value).strip() for value in allowed_values}
    bad: list[dict[str, Any]] = []
    missing = 0
    for index, row in enumerate(rows):
        raw = _row_get(row, headers, field)
        if raw is None:
            missing += 1
            continue
        if str(raw).strip() not in allowed:
            bad.append({"row": index + 1, "value": raw})
    if missing == len(rows):
        return [{"issue": "enum_field_missing", "message": f"所有行都找不到枚举字段 `{field}`。"}]
    if bad:
        return [{
            "issue": "enum_value_violation",
            "message": f"字段 `{field}` 存在 {len(bad)} 行不属于允许值 {sorted(allowed)}。",
            "examples": bad[:5],
        }]
    return []


def _guess_date_field(headers: list[str] | None, rows: list[Any]) -> str | None:
    if not headers:
        return None
    preferred = [header for header in headers if any(keyword in str(header) for keyword in ("日期", "时间", "date", "time"))]
    for header in preferred + headers:
        values = [_row_get(row, headers, str(header)) for row in rows[:10]]
        parseable = sum(1 for value in values if value is not None and re.search(r"\d{4}-\d{2}-\d{2}", str(value)))
        if parseable:
            return str(header)
    return None


def _guess_enum_field(headers: list[str] | None, rows: list[Any], allowed_values: list[str] | None) -> str | None:
    if not headers or not allowed_values:
        return None
    allowed = {value.strip() for value in allowed_values}
    best: tuple[int, str] | None = None
    for header in headers:
        values = [_row_get(row, headers, str(header)) for row in rows[:20]]
        hits = sum(1 for value in values if str(value).strip() in allowed)
        if hits and (best is None or hits > best[0]):
            best = (hits, str(header))
    return best[1] if best else None


_REQUIREMENT_ACTION_PREFIXES = ("抓取", "采集", "提取", "获取", "爬取", "导出", "下载", "统计", "收集")
_REQUIREMENT_GENERIC_SUFFIXES = ("数据", "信息", "内容", "结果", "模块", "页面", "列表", "表格", "报表", "明细")
# 去掉修饰后只剩这些词说明没指向任何具体业务对象，拿去比对只会制造噪音
_REQUIREMENT_STOP_TERMS = frozenset({
    "数据", "信息", "内容", "结果", "模块", "页面", "列表", "表格", "报表", "明细",
    "全部", "所有", "登录", "变量", "流程", "网站", "系统",
})


def _extract_requirement_targets(requirement_text: str) -> list[str]:
    """从需求文本里取出「要抓的是什么」的业务名词。"""
    # \S+ 会把 URL 后面紧跟的中文一起吃掉（中文不是空白），只截 URL 合法的 ASCII
    text = re.sub(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", " ", requirement_text or "")
    targets: list[str] = []
    for segment in re.split(r"[，,。；;：:、\n\r]+", text):
        term = segment.strip()
        for prefix in _REQUIREMENT_ACTION_PREFIXES:
            if term.startswith(prefix):
                term = term[len(prefix):].strip()
        # 「核心业务指标模块数据」要连剥两层才露出业务名
        changed = True
        while changed:
            changed = False
            for suffix in _REQUIREMENT_GENERIC_SUFFIXES:
                if len(term) > len(suffix) and term.endswith(suffix):
                    term = term[: -len(suffix)].strip()
                    changed = True
        if len(term) >= 2 and term not in _REQUIREMENT_STOP_TERMS and term not in targets:
            targets.append(term)
    return targets[:6]


def _check_requirement_alignment(
    targets: list[str],
    rows: list[Any],
    headers: list[str] | None,
) -> dict[str, Any] | None:
    """需求里的业务名词在实际输出里是否留下了痕迹。

    只比对真实数据（表头 / 字段名 / 单元格值），不看变量名和节点标题——那些是 AI
    自己起的名字，用需求词命名再拿来自证，正好会掩盖抽错表这类错误。
    """
    if not targets:
        return None
    surface: list[str] = [str(h) for h in (headers or [])]
    for row in rows[:60]:
        if isinstance(row, dict):
            surface.extend(str(k) for k in row)
            surface.extend(str(v) for v in row.values())
        elif isinstance(row, list):
            surface.extend(str(cell) for cell in row)
        else:
            surface.append(str(row))
    haystack = " ".join(surface)
    matched = [t for t in targets if _shares_substring(t, haystack)]
    return {"targets": targets, "matched": matched, "aligned": bool(matched)}


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


def _infer_constraints_from_requirement(requirement_text: str) -> dict[str, Any]:
    """从用户原始需求里提取通用约束，不绑定任何页面或字段名。"""
    text = requirement_text.strip()
    if not text:
        return {}
    inferred: dict[str, Any] = {}
    dates = re.findall(r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})", text)
    if dates:
        normalized = [f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in dates]
        inferred["start_date"] = normalized[0]
        if len(normalized) > 1:
            inferred["end_date"] = normalized[1]
        elif "今天" in text or "今日" in text:
            from datetime import datetime
            inferred["end_date"] = datetime.now().strftime("%Y-%m-%d")

    enum_match = re.search(r"(?:多选|状态|进度|类型|类别|分类)[：:]\s*([^，。,；;\n]+)", text)
    if enum_match:
        raw = enum_match.group(1)
        raw = re.sub(r"[（(]\s*多选\s*[）)]", "", raw)
        values = [part.strip() for part in re.split(r"[/、,，|]", raw) if part.strip()]
        if len(values) >= 2:
            inferred["allowed_values"] = values
    return inferred


def _build_quality_repair_plan(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 把 assert_run_output 发现的业务质量问题（如整表被摊平、字段缺失）翻译成
    # AI 可执行的结构化修复步骤，避免模型只收到问题描述却不知道该改哪个节点。
    plan: list[dict[str, Any]] = []
    issue_names = {str(issue.get("issue", "")) for issue in issues}
    if any(
        "whole_table_flattened" in name
        or "table_extract_selector_targets_container" in name
        or "table_extract_selector_not_table_like" in name
        for name in issue_names
    ):
        plan.append({
            "action": "fix_table_extraction_selector",
            "reason": "输出不是按行结构化表格，通常是 extract selector 指向表格容器而不是数据行。",
            "steps": [
                "调用 get_flow 找到 browser.extract 表格节点。",
                "将 selector 改为真实数据行选择器；标准表格优先 tbody tr，Element UI/Ant Design 从 inspect_page/page_layout 中找行 class。",
                "保持 extractMode='table'，补充 outputVariable 和 countVariable。",
                "重新运行后再次调用 assert_run_output。",
            ],
        })
    if any("header_row_length_mismatch" in name for name in issue_names):
        plan.append({
            "action": "fix_header_row_alignment",
            "reason": "表头列数和数据行列数不一致，写 Excel 时会造成表头和值错位。",
            "steps": [
                "优先让抽取节点直接输出 list[dict]，字段名来自同一行结构，不要把 headers 和 rows 分开猜测拼接。",
                "如果必须使用 headers + list[list]，确保每一行长度都与 headers 完全一致；缺失值用空字符串占位，多余 UI 列要在写入前剔除。",
                "检查 table/extract selector 是否混入表头、分页、按钮或展开行；必要时收窄到业务数据行。",
                "重新运行后调用 assert_run_output，确认不再出现 header_row_length_mismatch。",
            ],
        })
    if any("date_range_violation" in name for name in issue_names):
        plan.append({
            "action": "verify_date_filter_applied",
            "reason": "输出数据包含日期范围外的记录，说明筛选条件填入 UI 但未真实生效（组件内部状态未更新）。",
            "steps": [
                "**禁止只改日期 selector 后再跑一遍**——这只会重复得到未生效筛选的结果。",
                "上方 issue.examples 已包含实际越界的行和日期值（无需再调 get_run_output），直接判断筛选是否真实生效。",
                "调用 get_flow 找到日期筛选相关节点；如果存在 browser.fill 日期输入框，或日期 click 节点 selector 过宽，必须重建日期链路。",
                "调用 inspect_page(scope_selector=筛选区域容器)。**若返回 date_controls[].interaction_recipe，按 steps 重建节点（selector 直接用，日期文本和节点数量按本次任务改写）；若 date_controls 为空**，按同样思路自己搭：写日期 → 提交 → 回读 → 校验。",
                "优先键入日期文本（browser.fill fillMode='type' + browser.press Enter），它与运行当天无关；点日历格要求面板正好停在目标月份，且翻月次数绝不能写死。",
                "点日历格时单元格 selector 必须排除上/下月的单元格（Element UI 的 prev-month/next-month、Ant Design 的非 cell-in-view）。",
                "在查询按钮点击后增加 browser.wait 等待表格数据更新（waitForSelector 或 delayMs 2000），再执行 extract。",
                "重新运行后调用 assert_run_output，用 start_date/end_date 参数确认第一行和最后一行日期均在范围内。",
            ],
        })
    if any("enum_value_violation" in name for name in issue_names):
        plan.append({
            "action": "filter_header_rows_and_fix_enum_extraction",
            "reason": "输出中出现枚举字段值等于字段名（表头行被混入数据），或枚举值不在允许列表中。",
            "steps": [
                "上方 issue.examples 已包含实际不合法的行和枚举值，直接据此判断是表头混入还是枚举筛选未生效（无需再调 get_run_output）。",
                "首先确认是否混入了表头行：若 issue.examples 中行值等于字段名（如 '项目进度'），则 selector 覆盖了 thead。",
                "修复表头混入：将 selector 改为 tbody tr 或 tbody tr:not(:first-child)；"
                "若已是 tbody tr 仍出现表头，改用 extractMode='table'（自动过滤表头）。",
                "若是枚举筛选未生效（数据含'项目通过'以外的枚举值）：检查下拉筛选控件是否真实触发了查询——"
                "点击下拉选项后必须点击查询/确认按钮，否则前端只是选中了选项但未重新拉数据。",
                "重新运行后调用 assert_run_output 并传入 allowed_values 参数，确认枚举列所有值均合法。",
            ],
        })
    if any("mixed_ui_rows" in name for name in issue_names):
        plan.append({
            "action": "narrow_extraction_scope",
            "reason": "输出中混入日期面板、分页、按钮等非业务 UI 行，说明抽取范围过宽。",
            "steps": [
                "调用 inspect_page 查看业务数据区域与浮层/筛选控件的 DOM 边界。",
                "将抽取 selector 或 scope 收窄到业务数据容器内的真实数据项/数据行。",
                "避免使用 tbody tr、[role=row] 等全页面宽泛 selector，除非已经限定父容器。",
                "重新运行后调用 assert_run_output，确认 sample_rows 不再包含星期、日历数字、分页或按钮文本。",
            ],
        })
    if any("date_filter_missing_verification" in name for name in issue_names):
        plan.append({
            "action": "add_date_filter_verification_gate",
            "reason": "日期筛选没有回读校验：写入没真正提交给组件时，页面会返回全量数据，流程却成功结束。",
            "steps": [
                "调用 get_flow 找到日期写入节点（fill 或点日历格）及其后续节点。",
                "在写入之后补 browser.extract（extractMode='attribute'、attribute='value'、includeInResult=false）回读开始/结束日期输入框到变量。",
                "再补一个 script.python 节点比对回读值与目标日期，不一致时 raise SystemExit；日期段节点不要设 continueOnError=true。",
                "回读值确实没落下时才动交互方式：调用 inspect_page(scope_selector=筛选区域容器) 取 date_controls[].interaction_recipe，"
                "按 steps（键入日期文本 + Enter）重建；键入在扩展执行器下可能不提交组件模型，可改走 fallback_steps 或把流程切到 playwright 执行器。",
                "走点日历格路线时：单元格 selector 排除上/下月单元格，且必须先读面板标题定位当前年月，翻月次数不能写死。",
                "不要只增加 delayMs 或重复写同一个输入框。",
            ],
        })
    if any("date_trigger_selector_too_broad" in name for name in issue_names):
        plan.append({
            "action": "narrow_date_trigger_selector",
            "reason": "日期触发 selector 过宽，可能打开错误控件或错误日期面板，导致后续日期点击链路偏移。",
            "steps": [
                "调用 inspect_page(scope_selector=筛选区域容器)。**若返回 date_controls[].interaction_recipe，直接用 recipe.trigger 替换当前过宽的触发节点 selector，其余 selector 也按 recipe 更新。若 date_controls 为空**，从 inputs 字段选取唯一精确输入框替换，不要使用逗号候选、`placeholder*=` 或 `first-of-type`。",
                "修复后运行并调用 assert_run_output(start_date/end_date)，确认输出日期范围真实生效。",
            ],
        })
    if any("critical_action_continue_on_error" in name for name in issue_names):
        plan.append({
            "action": "stop_swallowing_critical_action_failures",
            "reason": "关键筛选/提交/导航动作失败后继续执行，会把根因伪装成后续等待或抽取失败。",
            "steps": [
                "调用 lint_flow 找到 issue=critical_action_continue_on_error 的节点。",
                "移除关键业务动作上的 continueOnError；仅保留在可选弹窗、登录检测、Cookie 横幅等可缺失节点上。",
                "若确实需要容错，必须在后续增加可验证校验节点，并让校验失败中断流程。",
                "重新运行失败时先看 get_run_error.swallowed_critical_failures，再决定修复前置动作还是末端抽取。",
            ],
        })
    if any("constraint_not_verifiable" in name or "field_missing" in name for name in issue_names):
        plan.append({
            "action": "make_output_verifiable",
            "reason": "用户约束无法在输出中定位字段，说明抽取结构或字段命名不足以验证业务正确性。",
            "steps": [
                "确保表头和数据行被结构化输出；必要时单独抽取表头并设置 includeInResult=false。",
                "使用 table 模式输出 list[list] 或 list[dict]，避免纯文本拼接。",
                "重新运行 assert_run_output，让工具自动从表头/行值匹配约束字段。",
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
    if "no_table_like_output" in issue_names:
        plan.append({
            "action": "produce_structured_rows",
            "reason": "整次运行没有任何可按行读取的变量，抓取结果无法验证。",
            "steps": [
                "先看本次返回的 observed_variables：那是实际产出，含每个变量不算表格的原因。",
                "若已有一个 list 里装的是纯文本，改抽取节点 extractMode='table' 让它直接产出 list[dict]，"
                "不要再加一个脚本节点把文本二次拼成表——那只是把同一个问题往后挪一格。",
                "若这次交付物本来就该是一篇文档，说明需求文本没体现出这一点："
                "直接向用户确认交付形态，不要造 rows 变量来迎合本条检查。",
            ],
        })
    if DOCUMENT_MISSING_RUN_DATA in issue_names:
        plan.append({
            "action": "wire_document_to_extracted_data",
            "reason": "文档写出来了，但正文里没有本次抓取到的任何原文，生成节点用的不是抽取节点的输出。",
            "steps": [
                "用 get_run_output 看抽取变量的实际值：先确认它非空、装的是正文而不是标题或计数。",
                "读生成节点的代码：它读的变量名是否就是那个抽取变量，写文件时写进去的是不是这个变量的内容。",
                "把需求原话写成文档标题对这条判据无效——它比的是抓取值，不是需求关键词。",
                "改完重跑，再调用本工具。",
            ],
        })
    # 上一条的出路不是自证：content_match_confirmed 解不开「正文里没有抓取数据」
    if any(name.startswith("document_") and name != DOCUMENT_MISSING_RUN_DATA for name in issue_names):
        plan.append({
            "action": "fix_document_content",
            "reason": "文档型交付物已写出，但内容量或内容本身对不上需求。",
            "steps": [
                "用 inspect_page 确认正文容器 selector 抓的是整篇内容，而非单个标题元素。",
                "分页/展开类内容确认循环真的翻完了，检查 countVariable 的实际值。",
                "确认文档内容确实是用户要的之后，传 content_match_confirmed=true 重新调用本工具。",
            ],
        })
    if not plan and issues:
        plan.append({
            "action": "inspect_and_repair_flow_structure",
            "reason": "运行质量审计失败，但没有匹配到专门修复模板。",
            "steps": [
                "调用 get_flow 和 lint_flow 查看结构风险。",
                "调用 get_run_output 查看实际变量形态。",
                "根据 issues 修复最靠前的结构性问题后重新运行。",
            ],
        })
    return plan


