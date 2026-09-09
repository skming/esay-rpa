"""按域名沉淀 RPA 助手的站点经验：哪些 selector 实际生效、是否需登录、UI 框架等，
存为 <ai_dir>/site_knowledge.json。后续对话提到同域名时，orchestrator 把画像注入
system context，让模型复用已验证的 selector 而不是重新猜测。

档案分两半：成功沉淀（哪些 selector 真跑通过）与失败沉淀（哪些 selector 真跑挂过）。
只留前一半的话，同一个坑换个流程就能被重踩一遍——[[ai_repair_ledger]] 手里有这份信息，
但它按 flow 存，跨流程抓同一站点时等于没学过。两者都只记真实运行结果，不记模型的判断。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app.core import storage
# 只沉淀真正在证伪 selector 的那两类诊断。元素存在但不可见的两类不算：
# 提示词里明确写着「这类问题改 selector 无效」，记成黑名单会把模型推向错误的修法。
from app.services.ai_tools.diagnostics import SELECTOR_FALSIFYING_KINDS

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s,，。？！\]）)\"']+")

_FRAMEWORK_HINTS = (
    ("el-", "Element UI"),
    ("ant-", "Ant Design"),
    ("arco-", "Arco Design"),
    ("van-", "Vant"),
    ("ivu-", "iView"),
    ("layui-", "Layui"),
    ("vxe-", "VXE Table"),
    ("tdesign-", "TDesign"),
    ("n-", "Naive UI"),
)

_MAX_SELECTORS_PER_TYPE = 12  # 每种节点类型只留最近验证过的若干个，避免画像随流程迭代无限膨胀
_MAX_DOMAINS = 200  # 超出后淘汰最久未更新的域名，防止 JSON 文件无限增长
_MAX_PAGES_PER_DOMAIN = 20  # 同一域名下只留最近验证过的若干个页面
_MAX_FAILED_SELECTORS = 20
# 失败比成功过期得快：selector 跑通说明它当时确实指对了元素，而跑挂可能只是页面那天在改版。
# 两周后还拿它当禁令，就会挡住页面已经改回去的正确答案。
_FAILED_TTL_SECONDS = 14 * 24 * 3600


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _domain_of(url: str) -> str | None:
    try:
        host = urlparse(url).hostname
    except Exception:
        return None
    return host.lower() if host else None


def _page_key(url: str) -> str | None:
    """页面粒度的归属键：host + path + hash。

    只按域名记经验，会把同一站点的「订单列表」和「报表」当成同一个页面。这两页的查询按钮
    常常 class 同名而结构不同，把订单页验证过的 selector 直接给报表页用，运行时报的是元素
    超时，从报错里看不出「这条经验本来就不属于这个页面」。SPA 后台的路由多半在 hash 里，
    所以 hash 必须算进键，否则整个后台仍然只有一个页面。query 不算：翻页、排序参数会把同
    一个页面拆成无数个键。
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = parsed.hostname
    if not host:
        return None
    path = (parsed.path or "/").rstrip("/") or "/"
    fragment = parsed.fragment.split("?")[0].rstrip("/") if parsed.fragment else ""
    key = f"{host.lower()}{path}" + (f"#{fragment}" if fragment else "")
    return key[:160]


def _pages_by_node(nodes: list[Any], edges: Any) -> dict[str, str | None]:
    """每个节点执行时停在哪个页面。

    节点自己带 targetUrl 就以它为准；否则沿边继承上游的页面——browser.click 这类节点不带
    URL，它所在的页面是上一次导航留下的。两条分支汇合处继承到的页面不一致就归属为 None，
    退回域名级：记错页面比没记更糟，模型会拿着「已验证」的字样把 selector 写进另一个页面。
    """
    nodes_by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for node in nodes:
        if isinstance(node, dict) and node.get("id"):
            nodes_by_id[str(node["id"])] = node
            order.append(str(node["id"]))

    def own_page(node: dict[str, Any]) -> str | None:
        target = node.get("targetUrl")
        if isinstance(target, str) and target.startswith("http"):
            return _page_key(target)
        return None

    adjacency: dict[str, list[str]] = {}
    for edge in edges if isinstance(edges, list) else []:
        if isinstance(edge, dict) and edge.get("source") and edge.get("target"):
            adjacency.setdefault(str(edge["source"]), []).append(str(edge["target"]))

    reached: dict[str, set[str | None]] = {}
    if adjacency:
        targets = {t for outs in adjacency.values() for t in outs}
        # 入口 = 没有任何边指向它的节点，不写死 start：分支流程里入口不一定叫 start。
        stack: list[tuple[str, str | None]] = [(nid, None) for nid in order if nid not in targets]
        # 以 (节点, 继承页面) 去重而不是只按节点：foreach 的回边会绕回同一节点，
        # 只按节点去重会漏掉「同一节点在另一条分支上属于另一个页面」这个歧义。
        seen: set[tuple[str, str | None]] = set()
        while stack:
            nid, inherited = stack.pop()
            if (nid, inherited) in seen:
                continue
            seen.add((nid, inherited))
            node = nodes_by_id.get(nid)
            if node is None:
                continue
            page = own_page(node) or inherited
            reached.setdefault(nid, set()).add(page)
            for nxt in adjacency.get(nid, []):
                stack.append((nxt, page))
    else:
        # 没有边时按节点顺序推断：这是流程定义里仅剩的顺序信息。
        current: str | None = None
        for nid in order:
            current = own_page(nodes_by_id[nid]) or current
            reached[nid] = {current}

    return {nid: (next(iter(pages)) if len(pages) == 1 else None) for nid, pages in reached.items()}


def extract_urls(text: str) -> list[str]:
    seen: list[str] = []
    for m in _URL_RE.findall(text or ""):
        if m not in seen:
            seen.append(m)
    return seen


def extract_domains(text: str) -> list[str]:
    seen: list[str] = []
    for m in _URL_RE.findall(text or ""):
        d = _domain_of(m)
        if d and d not in seen:
            seen.append(d)
    return seen


def _fresh_failures(raw: Any) -> list[dict[str, Any]]:
    """过滤掉过期与格式不对的失败记录；读写都先过一遍，避免陈旧禁令越积越多。"""
    if not isinstance(raw, list):
        return []
    now = time.time()
    return [
        item for item in raw
        if isinstance(item, dict)
        and item.get("selector")
        and now - float(item.get("at_ts") or 0) <= _FAILED_TTL_SECONDS
    ]


def _guess_framework(selectors: list[str]) -> str | None:
    joined = " ".join(selectors)
    for prefix, name in _FRAMEWORK_HINTS:
        if f".{prefix}" in joined or f"[class*='{prefix}" in joined:
            return name
    return None


class SiteKnowledgeStore:
    def __init__(self, path: str | None = None) -> None:
        self._path = path or str(storage.resolve_ai_dir() / "site_knowledge.json")
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        except Exception as exc:  # pragma: no cover — corrupt disk etc.
            logger.warning("site_knowledge load failed: %s", exc)
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        # 域名数超限时淘汰最久未更新的
        if len(data) > _MAX_DOMAINS:
            ordered = sorted(data.items(), key=lambda kv: kv[1].get("updated_at", ""), reverse=True)
            data = dict(ordered[:_MAX_DOMAINS])
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except Exception as exc:  # pragma: no cover
            logger.warning("site_knowledge save failed: %s", exc)

    def record_flow_success(self, flow_definition: dict[str, Any], flow_name: str | None = None) -> None:
        nodes = flow_definition.get("nodes") or []
        if not isinstance(nodes, list):
            return

        domains: list[str] = []
        urls: list[str] = []
        url_by_page: dict[str, str] = {}
        for node in nodes:
            if not isinstance(node, dict):
                continue
            target = node.get("targetUrl")
            if isinstance(target, str) and target.startswith("http"):
                d = _domain_of(target)
                if d:
                    urls.append(target)
                    if d not in domains:
                        domains.append(d)
                    key = _page_key(target)
                    if key:
                        url_by_page.setdefault(key, target)
        if not domains:
            return

        pages_by_node = _pages_by_node(nodes, flow_definition.get("edges"))
        selectors_by_type: dict[str, list[str]] = {}
        selectors_by_page: dict[str, dict[str, list[str]]] = {}
        all_selectors: list[str] = []
        has_login_fill = False
        for node in nodes:
            if not isinstance(node, dict):
                continue
            ntype = str(node.get("type") or "")
            sel = node.get("selector")
            if isinstance(sel, str) and sel.strip() and ntype.startswith(("browser.", "ui.")):
                bucket = selectors_by_type.setdefault(ntype, [])
                if sel not in bucket:
                    bucket.append(sel)
                all_selectors.append(sel)
                page = pages_by_node.get(str(node.get("id") or ""))
                if page:
                    page_bucket = selectors_by_page.setdefault(page, {}).setdefault(ntype, [])
                    if sel not in page_bucket:
                        page_bucket.append(sel)
            if ntype == "browser.fill":
                value = str(node.get("inputValue") or "")
                if "password" in str(sel or "") or "${var.password}" in value:
                    has_login_fill = True

        # ensureLogin 的已登录探针是站点级事实，单独沉淀供后续流程复用。
        logged_in_probe: str | None = None
        for node in nodes:
            if isinstance(node, dict) and str(node.get("type") or "") == "browser.ensureLogin":
                sel = node.get("selector")
                if isinstance(sel, str) and sel.strip():
                    logged_in_probe = sel.strip()
                    break

        framework = _guess_framework(all_selectors)

        with self._lock:
            data = self._load()
            for domain in domains:
                profile = data.get(domain) or {
                    "domain": domain,
                    "success_count": 0,
                    "selectors": {},
                    "verified_urls": [],
                }
                profile["success_count"] = int(profile.get("success_count", 0)) + 1
                profile["updated_at"] = _now_iso()
                if flow_name:
                    profile["last_flow_name"] = flow_name
                if framework:
                    profile["framework"] = framework
                if has_login_fill:
                    profile["requires_login"] = True
                if logged_in_probe:
                    profile["logged_in_probe"] = logged_in_probe
                merged: dict[str, list[str]] = profile.get("selectors") or {}
                for ntype, sels in selectors_by_type.items():
                    bucket = merged.setdefault(ntype, [])
                    for sel in sels:
                        if sel not in bucket:
                            bucket.append(sel)
                    merged[ntype] = bucket[-_MAX_SELECTORS_PER_TYPE:]  # 保留最近的
                profile["selectors"] = merged
                pages: dict[str, Any] = profile.get("pages") or {}
                for page_key, sels in selectors_by_page.items():
                    if not page_key.startswith(domain + "/"):
                        continue  # 跨域流程：另一个域名的页面不能记到这个域名的档案里
                    entry = pages.get(page_key) or {"selectors": {}, "success_count": 0}
                    entry["success_count"] = int(entry.get("success_count", 0)) + 1
                    entry["updated_at"] = _now_iso()
                    if url_by_page.get(page_key):
                        entry["url"] = url_by_page[page_key]
                    page_merged: dict[str, list[str]] = entry.get("selectors") or {}
                    for ntype, page_sels in sels.items():
                        bucket = page_merged.setdefault(ntype, [])
                        for sel in page_sels:
                            if sel not in bucket:
                                bucket.append(sel)
                        page_merged[ntype] = bucket[-_MAX_SELECTORS_PER_TYPE:]
                    entry["selectors"] = page_merged
                    pages[page_key] = entry
                if len(pages) > _MAX_PAGES_PER_DOMAIN:
                    ordered_pages = sorted(
                        pages.items(), key=lambda kv: str(kv[1].get("updated_at") or ""), reverse=True
                    )
                    pages = dict(ordered_pages[:_MAX_PAGES_PER_DOMAIN])
                profile["pages"] = pages
                # 跑通即撤销禁令：能跑通说明当时挂的是别的原因（时序、登录态、页面在改版），
                # 禁令留着会让模型绕开这个正确答案去猜别的写法。
                profile["failed_selectors"] = [
                    f for f in _fresh_failures(profile.get("failed_selectors"))
                    if f.get("selector") not in all_selectors
                ]
                verified = profile.get("verified_urls") or []
                for u in urls:
                    if _domain_of(u) == domain and u not in verified:
                        verified.append(u)
                profile["verified_urls"] = verified[-10:]
                data[domain] = profile
            self._save(data)
        logger.info("site_knowledge updated for domains: %s", domains)

    def record_selector_failure(
        self,
        url: str | None,
        selector: str,
        *,
        node_type: str = "",
        diagnostic_kind: str = "",
    ) -> None:
        """记下「这个 selector 在这个站点真的跑挂过」。

        调用点必须持有真实失败现场（get_run_error 的 selector_diagnostic），
        不接受模型的判断——否则档案会变成模型自我强化的猜测，而不是证据。
        """
        domain = _domain_of(url or "")
        selector = (selector or "").strip()
        if not domain or not selector or diagnostic_kind not in SELECTOR_FALSIFYING_KINDS:
            return

        with self._lock:
            data = self._load()
            profile = data.get(domain) or {
                "domain": domain,
                "success_count": 0,
                "selectors": {},
                "verified_urls": [],
            }
            failures = _fresh_failures(profile.get("failed_selectors"))
            for item in failures:
                # 同一个 selector 重复踩不占新条目：次数本身就是最强的信号
                if item.get("selector") == selector:
                    item["count"] = int(item.get("count") or 1) + 1
                    item["at_ts"] = time.time()
                    item["kind"] = diagnostic_kind
                    break
            else:
                failures.append({
                    "selector": selector,
                    "node_type": node_type,
                    "kind": diagnostic_kind,
                    "count": 1,
                    "at_ts": time.time(),
                })
            profile["failed_selectors"] = failures[-_MAX_FAILED_SELECTORS:]
            profile["updated_at"] = _now_iso()
            data[domain] = profile
            self._save(data)

    def get_profile(self, domain: str) -> dict[str, Any] | None:
        with self._lock:
            return self._load().get(domain.lower())

    def match_text(self, text: str) -> list[dict[str, Any]]:
        domains = extract_domains(text)
        if not domains:
            return []
        with self._lock:
            data = self._load()
        return [data[d] for d in domains if d in data]

    @staticmethod
    def build_context_message(profiles: list[dict[str, Any]], urls: list[str] | None = None) -> str:
        """urls 是本轮对话里出现的 URL，用来把经验分成「当前页面验证过」和「同域其它页面验证过」。

        不传就退回域名级展示（旧档案没有 pages 字段时也走这条），此时不会声称任何 selector
        属于当前页面。
        """
        wanted_pages = [k for k in (_page_key(u) for u in urls or []) if k]
        lines = [
            "## 站点经验档案（来自该站点历史成功运行，优先复用以下已验证信息）",
            "",
        ]
        for p in profiles:
            lines.append(f"### {p.get('domain')}（成功运行 {p.get('success_count', 0)} 次）")
            if p.get("framework"):
                lines.append(f"- UI 框架：{p['framework']}")
            if p.get("requires_login"):
                lines.append("- 该站点需要登录（历史流程包含账号密码填写）")
            if p.get("logged_in_probe"):
                lines.append(
                    f"- 已验证的登录态探针：`{p['logged_in_probe']}`"
                    "（browser.ensureLogin 的 selector 直接用它）"
                )
            verified = p.get("verified_urls") or []
            if verified:
                lines.append("- 已验证可达的 URL：" + "、".join(f"`{u}`" for u in verified[-5:]))
            pages = p.get("pages") or {}
            current_keys = [k for k in wanted_pages if k in pages]
            for key in current_keys:
                entry = pages.get(key) or {}
                page_sels = entry.get("selectors") or {}
                if not page_sels:
                    continue
                lines.append(
                    f"- **当前页面** `{entry.get('url') or key}` 上已验证的 selector"
                    f"（在这个页面跑通 {int(entry.get('success_count') or 1)} 次，可直接复用）："
                )
                for ntype, sels in page_sels.items():
                    lines.append(f"  - {ntype}: " + "、".join(f"`{s}`" for s in sels[-4:]))
            others = [k for k in pages if k not in set(current_keys)]
            if others:
                lines.append(
                    "- 同域**其它页面**验证过的 selector（**未必适用当前页**：同一站点不同页面"
                    "常常 class 同名而结构不同，照抄前必须 inspect_page 确认它在当前页面存在）："
                )
                for key in sorted(
                    others, key=lambda k: str((pages.get(k) or {}).get("updated_at") or ""), reverse=True
                )[:4]:
                    flat = [
                        s for sels in ((pages.get(key) or {}).get("selectors") or {}).values() for s in sels
                    ][-3:]
                    if flat:
                        lines.append(f"  - `{key}`：" + "、".join(f"`{s}`" for s in flat))
            # 页面级已经展示过的不再重复；剩下的是归属不到具体页面的（分支汇合处、旧档案）。
            shown_selectors = {
                s
                for entry in pages.values()
                for sels in ((entry or {}).get("selectors") or {}).values()
                for s in sels
            }
            residual = {
                ntype: [s for s in sels if s not in shown_selectors]
                for ntype, sels in (p.get("selectors") or {}).items()
            }
            residual = {ntype: sels for ntype, sels in residual.items() if sels}
            if residual:
                # 判据是「这条 selector 的页面未知」，不是「这个档案有没有页面记录」：分支汇合处的
                # selector 两个页面都可能，这份不确定不写进标签，模型就会当成当前页面已验证。
                lines.append("- 同域已验证、但归属不到具体页面的 selector（用前先确认它在当前页面存在）：")
                for ntype, sels in residual.items():
                    shown = "、".join(f"`{s}`" for s in sels[-4:])
                    lines.append(f"  - {ntype}: {shown}")
            failures = _fresh_failures(p.get("failed_selectors"))
            if failures:
                lines.append("- 已证伪的 selector（真实运行时未命中，换个写法再试大概率还是这个结果）：")
                for f in sorted(failures, key=lambda x: -int(x.get("count") or 1))[:6]:
                    times = int(f.get("count") or 1)
                    suffix = f"（已踩 {times} 次）" if times > 1 else ""
                    lines.append(f"  - `{f['selector']}` — {f.get('kind') or '运行失败'}{suffix}")
            lines.append("")
        lines.append(
            "以上 selector 均来自真实运行结果。构建/修复同站点流程时**先用当前页面那一组**，"
            "只有页面确实改版（inspect_page 证实旧 selector 不存在）时才替换；其它页面那一组只是候选，"
            "**已证伪的不要再写进流程**，除非这次 inspect_page 亲眼看到它确实存在。"
        )
        return "\n".join(lines)


_default_store: SiteKnowledgeStore | None = None


def get_site_knowledge_store() -> SiteKnowledgeStore:
    global _default_store
    if _default_store is None:
        _default_store = SiteKnowledgeStore()
    return _default_store
