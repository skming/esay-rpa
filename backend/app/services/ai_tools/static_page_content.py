"""静态观察的结构化摘要和按对话归属的短期快照。"""
from __future__ import annotations

import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterator

from lxml import html

import app.services.ai_tools.page_session as page_session

# 正文预算沿用已有上限；按完整记录装入，而非对整个页面直接切片。
CONTENT_BUDGET = 6_000
_BLOCK_TAGS = {"p", "li", "pre", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "table"}
_CONTAINER_TAGS = {"html", "body", "main", "article", "section", "div", "ul", "ol", "header", "footer", "nav", "aside", "dl", "dt", "dd", "form"}


@dataclass(frozen=True)
class ContentBlock:
    text: str
    atomic: bool = False
    header: str = ""
    group: str = ""


def _markdown(element: Any) -> str:
    from scrapling.core.shell import Convertor

    parent = element.getparent()
    if element.tag == "li" and parent is not None and parent.tag == "ol":
        siblings = parent.findall("li")
        step = -1 if "reversed" in parent.attrib else 1
        number = len(siblings) if step == -1 else 1
        try:
            number = int(parent.get("start", number))
        except ValueError:
            pass
        for sibling in siblings:
            try:
                number = int(sibling.get("value", number))
            except ValueError:
                pass
            if sibling is element:
                break
            number += step
        wrapper = html.Element("ol", start=str(number))
        wrapper.append(deepcopy(element))
        element = wrapper
    return Convertor._convert_to_markdown(html.tostring(element, encoding="unicode", with_tail=False)).strip()


def _table_blocks(table: Any) -> Iterator[ContentBlock]:
    # 合并单元格跨行关联，不能拆成互相独立的数据行。
    if table.xpath('.//*[@rowspan and @rowspan!="1"] | .//*[@colspan and @colspan!="1"] | .//table'):
        yield ContentBlock(_markdown(table), atomic=True)
        return
    for caption in table.xpath('./caption'):
        yield ContentBlock(_markdown(caption))
    rows = table.xpath('./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr')
    if not rows:
        yield ContentBlock(_markdown(table), atomic=True)
        return
    heading = rows[0] if rows[0].xpath('./th') else None
    shell = html.Element("table")
    if heading is not None:
        shell.append(deepcopy(heading))
    header = _markdown(shell) if heading is not None else ""
    group = uuid.uuid4().hex
    if heading is not None and len(rows) == 1:
        yield ContentBlock(header, atomic=True)
    for row in rows:
        if row is heading:
            continue
        current = deepcopy(shell)
        current.append(deepcopy(row))
        rendered = _markdown(current)
        if header and rendered.startswith(header):
            rendered = rendered[len(header):].lstrip('\n')
        yield ContentBlock(rendered, atomic=True, header=header, group=group)


def _walk(element: Any) -> Iterator[ContentBlock]:
    if element.tag == "table":
        yield from _table_blocks(element)
    elif element.tag in _BLOCK_TAGS:
        yield ContentBlock(_markdown(element), atomic=element.tag in {"li", "pre", "blockquote"})
    else:
        # 收集同一容器里的连续行内内容，保留链接与强调，遇到块级子元素才分段。
        inline = html.Element("div")
        inline.text = element.text
        for child in element:
            if (child.tag in _BLOCK_TAGS or child.tag in _CONTAINER_TAGS
                    or any(node.tag in _BLOCK_TAGS for node in child.iterdescendants())):
                text = _markdown(inline)
                if text:
                    yield ContentBlock(text)
                yield from _walk(child)
                inline = html.Element("div")
                inline.text = child.tail
            else:
                inline.append(deepcopy(child))
        text = _markdown(inline)
        if text:
            yield ContentBlock(text)


def content_blocks(document: Any, url: str, scope_selector: str | None) -> list[ContentBlock]:
    root = deepcopy(document)
    # hidden 属性尚未被 Scrapling 0.4.15 的清理器覆盖。
    for element in root.xpath('//*[@hidden]'):
        element.drop_tree()
    from scrapling.core.shell import Convertor
    from scrapling.parser import Selector

    # Response.markdown() 仅返回字符串。沿用它的清理步骤取得 DOM，才能先按记录分块；
    # 升级 Scrapling 时由隐藏内容、表格和列表回归测试约束这个内部接口。
    selector = Selector(content=html.tostring(root, encoding="unicode"), url=url)
    clean = Convertor._sanitize_for_ai(Convertor._strip_noise_tags(selector))
    root = html.fromstring(clean.html_content)
    if scope_selector is not None:
        matches = root.cssselect(scope_selector)
        if len(matches) != 1:
            raise ValueError(f"scope_selector 必须唯一命中容器，当前命中 {len(matches)} 个；请缩小或修正范围")
        root = matches[0]
    else:
        bodies = root.xpath('//body')
        root = bodies[0] if bodies else root
    return [block for block in _walk(root) if block.text]


@dataclass
class StaticSnapshot:
    document: Any
    evidence: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    touched: float = field(default_factory=time.monotonic)
    views: dict[str | None, list[ContentBlock]] = field(default_factory=dict)
    cursors: dict[str, tuple[str | None, int, int]] = field(default_factory=dict)

    def read(self, scope_selector: str | None = None, cursor: str | None = None) -> dict[str, Any]:
        index = offset = 0
        if cursor is not None:
            position = self.cursors.get(cursor)
            if position is None:
                raise ValueError("cursor 不属于当前快照，请使用上次返回的 next_cursor")
            scope, index, offset = position
            if scope_selector is not None and scope_selector != scope:
                raise ValueError("cursor 的范围与 scope_selector 不一致；切换范围时请省略 cursor")
            scope_selector = scope
        if scope_selector not in self.views:
            self.views[scope_selector] = content_blocks(self.document, self.evidence["url"], scope_selector)
        blocks = self.views[scope_selector]
        if not blocks:
            raise ValueError("静态抓取未取得可用正文")
        output = ""
        group = ""
        partial = False
        while index < len(blocks):
            block = blocks[index]
            prefix = ("\n\n" if output else "")
            if block.header and group != block.group:
                prefix += block.header + "\n"
            rest = block.text[offset:]
            available = CONTENT_BUDGET - len(output) - len(prefix)
            if len(rest) > available:
                if output:
                    break
                if block.atomic or available <= 0:
                    raise ValueError("单条列表项、代码块或表格行超过摘要预算，请用 scope_selector 缩小到具体字段")
                output = prefix + rest[:available]
                offset += available
                partial = True
                break
            output += prefix + rest
            partial = partial or offset > 0
            index += 1
            offset = 0
            group = block.group
        truncated = index < len(blocks)
        next_cursor = None
        if truncated:
            next_cursor = uuid.uuid4().hex
            self.cursors[next_cursor] = (scope_selector, index, offset)
        self.touched = time.monotonic()
        result = dict(self.evidence)
        result.update(page_text_sample=output, snapshot_id=self.id, truncated=truncated, next_cursor=next_cursor)
        if partial:
            result["partial_block"] = True
        if scope_selector is not None:
            result["scope_selector"] = scope_selector
        return result


_snapshots: dict[str, StaticSnapshot] = {}


def clear_static_snapshot(token: str | None = None) -> None:
    _snapshots.pop(page_session.current_owner(token), None)


def save_static_snapshot(snapshot: StaticSnapshot) -> None:
    now = time.monotonic()
    for owner, current in list(_snapshots.items()):
        if now - current.touched > page_session.IDLE_TTL_SECONDS:
            _snapshots.pop(owner, None)
    _snapshots[page_session.current_owner()] = snapshot


def read_static_snapshot(snapshot_id: str, scope_selector: str | None = None, cursor: str | None = None) -> dict[str, Any]:
    snapshot = _snapshots.get(page_session.current_owner())
    if snapshot is None or snapshot.id != snapshot_id or time.monotonic() - snapshot.touched > page_session.IDLE_TTL_SECONDS:
        if snapshot is not None and time.monotonic() - snapshot.touched > page_session.IDLE_TTL_SECONDS:
            clear_static_snapshot()
        return {"status": "error", "error": "静态快照已失效或不属于当前对话，请重新 inspect_page(url=...)",
                "required_action": "call_inspect_page_with_url"}
    try:
        return snapshot.read(scope_selector, cursor)
    except Exception as exc:
        return {"status": "error", "error": f"静态摘要读取失败：{exc}"}
