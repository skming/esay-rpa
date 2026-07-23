"""运行结果诊断：失败根因推断、输出数据质量断言、修复方案生成。

面向"流程跑完之后"的分析，与面向"跑之前"的 lint 互补。
"""
from __future__ import annotations

import json
import re
from typing import Any

_NAVIGATION_NODE_TYPES = frozenset({"browser.open", "browser.ensureLogin"})


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


def _detect_mixed_ui_rows(rows: list[Any]) -> dict[str, Any] | None:
    """检测抓取结果是否混入日期面板、分页、按钮等非业务 UI 行。"""
    ui_like_indexes: list[int] = []
    for index, row in enumerate(rows[:80]):
        values: list[str]
        if isinstance(row, dict):
            values = [str(value).strip() for value in row.values()]
        elif isinstance(row, list):
            values = [str(value).strip() for value in row]
        else:
            continue
        compact = [value for value in values if value]
        if not compact:
            continue
        joined = "|".join(compact)
        weekday_set = {"日", "一", "二", "三", "四", "五", "六"}
        if len(compact) == 7 and set(compact) <= weekday_set:
            ui_like_indexes.append(index)
            continue
        numeric_cells = sum(1 for value in compact if re.fullmatch(r"\d{1,2}", value))
        if len(compact) in {6, 7} and numeric_cells >= 5:
            ui_like_indexes.append(index)
            continue
        if any(token in joined for token in ("上一页", "下一页", "确定", "取消", "今天", "清空")):
            ui_like_indexes.append(index)
            continue
    if not ui_like_indexes:
        return None
    return {
        "issue": "mixed_ui_rows",
        "message": (
            "结果中混入了日历、分页、按钮或其他非业务 UI 控件行。"
            "这说明抽取 selector/scope 过宽，虽然有行数据，但不能证明业务筛选和字段约束可信。"
        ),
        "ui_like_row_indexes": ui_like_indexes[:10],
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


