"""按域名沉淀 RPA 助手的站点经验：哪些 selector 实际生效、是否需登录、UI 框架等，
存为 <ai_dir>/site_knowledge.json。后续对话提到同域名时，orchestrator 把画像注入
system context，让模型复用已验证的 selector 而不是重新猜测。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app.core import storage

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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _domain_of(url: str) -> str | None:
    try:
        host = urlparse(url).hostname
    except Exception:
        return None
    return host.lower() if host else None


def extract_domains(text: str) -> list[str]:
    seen: list[str] = []
    for m in _URL_RE.findall(text or ""):
        d = _domain_of(m)
        if d and d not in seen:
            seen.append(d)
    return seen


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
        if not domains:
            return

        selectors_by_type: dict[str, list[str]] = {}
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
                verified = profile.get("verified_urls") or []
                for u in urls:
                    if _domain_of(u) == domain and u not in verified:
                        verified.append(u)
                profile["verified_urls"] = verified[-10:]
                data[domain] = profile
            self._save(data)
        logger.info("site_knowledge updated for domains: %s", domains)

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
    def build_context_message(profiles: list[dict[str, Any]]) -> str:
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
            selectors = p.get("selectors") or {}
            if selectors:
                lines.append("- 已验证有效的 selector（按节点类型）：")
                for ntype, sels in selectors.items():
                    shown = "、".join(f"`{s}`" for s in sels[-4:])
                    lines.append(f"  - {ntype}: {shown}")
            lines.append("")
        lines.append(
            "以上 selector 均来自真实成功运行。构建/修复同站点流程时**优先直接复用**，"
            "只有页面确实改版（inspect_page 证实旧 selector 不存在）时才替换。"
        )
        return "\n".join(lines)


_default_store: SiteKnowledgeStore | None = None


def get_site_knowledge_store() -> SiteKnowledgeStore:
    global _default_store
    if _default_store is None:
        _default_store = SiteKnowledgeStore()
    return _default_store
