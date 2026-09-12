from __future__ import annotations

import pytest
from lxml import html

import app.services.ai_tools.page_session as sessions
import app.services.ai_tools.static_page_content as content


def snapshot(markup):
    return content.StaticSnapshot(html.fromstring(markup), {
        "status": "success", "url": "https://example.com/", "inspection_source": "scrapling_static",
        "static_only": True,
    })


def all_pages(item, scope=None):
    pages = [item.read(scope)]
    while pages[-1]["truncated"]:
        assert len(pages) < 100
        pages.append(item.read(cursor=pages[-1]["next_cursor"]))
    assert all(len(p["page_text_sample"]) <= content.CONTENT_BUDGET for p in pages)
    assert pages[-1]["next_cursor"] is None
    return pages


def test_paragraph_and_reply_boundaries(monkeypatch):
    monkeypatch.setattr(content, "CONTENT_BUDGET", 100)
    records = [f"正文{i:02d}" + "内容" * 20 for i in range(9)]
    item = snapshot('<body><article>' + ''.join(f'<p>{x}</p>' for x in records) +
                    '</article><ul><li>回复一</li><li>回复二</li></ul></body>')
    pages = all_pages(item)
    assert len(pages) > 1
    for record in records + ["回复一", "回复二"]:
        assert sum(record in page["page_text_sample"] for page in pages) == 1
    cursor = pages[0]["next_cursor"]
    assert item.read(cursor=cursor)["page_text_sample"] == pages[1]["page_text_sample"]


def test_table_header_is_repeated_but_rows_are_not(monkeypatch):
    monkeypatch.setattr(content, "CONTENT_BUDGET", 105)
    rows = [f'<tr><td>单号{i:03d}</td><td>{i}.50</td></tr>' for i in range(12)]
    item = snapshot('<table><caption>账单</caption><thead><tr><th>单号</th><th>金额</th></tr></thead>'
                    '<tbody>' + ''.join(rows) + '</tbody></table>')
    pages = all_pages(item)
    assert len(pages) > 1
    for page in pages:
        assert "金额" in page["page_text_sample"]
    for i in range(12):
        assert sum(f"单号{i:03d}" in p["page_text_sample"] for p in pages) == 1


def test_long_paragraph_continues_without_dropping_characters(monkeypatch):
    monkeypatch.setattr(content, "CONTENT_BUDGET", 100)
    text = "正文" * 151
    pages = all_pages(snapshot(f'<p>{text}</p>'))
    assert ''.join(p["page_text_sample"] for p in pages) == text
    assert all(p.get("partial_block") for p in pages)


def test_oversized_record_requests_narrower_scope(monkeypatch):
    monkeypatch.setattr(content, "CONTENT_BUDGET", 100)
    item = snapshot('<table id="large"><tr><th>字段</th></tr><tr><td><p id="field">' +
                    '数据' * 100 + '</p></td></tr></table>')
    with pytest.raises(ValueError, match="缩小到具体字段"):
        item.read()
    assert len(all_pages(item, '#field')) == 2


def test_scope_is_unique_and_cursor_cannot_change_scope(monkeypatch):
    monkeypatch.setattr(content, "CONTENT_BUDGET", 100)
    item = snapshot('<main id="a"><p>' + '正文' * 100 +
                    '</p></main><aside id="b"><p>旁栏</p></aside>')
    first = item.read('#a')
    assert "旁栏" not in first["page_text_sample"]
    with pytest.raises(ValueError, match="不一致"):
        item.read('#b', first["next_cursor"])
    for selector in ('#missing', 'p'):
        with pytest.raises(ValueError, match="必须唯一"):
            item.read(selector)
    content.save_static_snapshot(item)
    assert content.read_static_snapshot(item.id, '[')["status"] == "error"
    with pytest.raises(ValueError, match="不属于当前快照"):
        item.read(cursor='invented')


async def test_owner_refresh_expiry_and_close_invalidate_snapshots(monkeypatch):
    first = snapshot('<p>对话一</p>')
    token = sessions.set_owner('first-summary-test')
    try:
        content.save_static_snapshot(first)
        other = sessions.set_owner('second-summary-test')
        try:
            second = snapshot('<p>对话二</p>')
            content.save_static_snapshot(second)
            assert content.read_static_snapshot(first.id)["status"] == "error"
            await sessions.close_current(token='first-summary-test')
            assert content.read_static_snapshot(second.id)["status"] == "success"
        finally:
            content.clear_static_snapshot()
            sessions.reset_owner(other)
        assert content.read_static_snapshot(first.id)["status"] == "error"
        content.save_static_snapshot(first)
        newer = snapshot('<p>重新观察</p>')
        content.save_static_snapshot(newer)
        assert content.read_static_snapshot(first.id)["status"] == "error"
        monkeypatch.setattr(content.time, 'monotonic', lambda: newer.touched + sessions.IDLE_TTL_SECONDS + 1)
        assert content.read_static_snapshot(newer.id)["status"] == "error"
    finally:
        content.clear_static_snapshot()
        sessions.reset_owner(token)


def test_ordered_list_continuation_keeps_item_numbers(monkeypatch):
    monkeypatch.setattr(content, "CONTENT_BUDGET", 50)
    item = snapshot('<ol start="5">' + ''.join(f'<li>事项{i}' + '内容' * 12 + '</li>' for i in range(4)) + '</ol>')
    pages = all_pages(item)
    assert len(pages) == 4
    for i, page in enumerate(pages):
        assert page["page_text_sample"].startswith(f"{i + 5}. 事项{i}")


def test_hidden_ancestor_and_custom_wrappers_do_not_leak_or_break_rows(monkeypatch):
    monkeypatch.setattr(content, "CONTENT_BUDGET", 70)
    item = snapshot('<body><div style="display:none"><p>HIDDEN</p></div><custom-element><section>'
                    + ''.join(f'<p>段落{i}' + '正文' * 15 + '</p>' for i in range(4))
                    + '</section></custom-element></body>')
    pages = all_pages(item)
    assert len(pages) > 1
    assert all('HIDDEN' not in p["page_text_sample"] for p in pages)
    assert all(sum(f"段落{i}" in p["page_text_sample"] for p in pages) == 1 for i in range(4))
