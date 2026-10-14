from __future__ import annotations

import pytest

from app.services.pagination_runtime import PaginationPage, collect_paginated_pages, pagination_max_pages


class PageSource:
    def __init__(self, pages, *, terminal=None):
        self.pages = pages
        self.terminal = terminal
        self.page = 0
        self.reads = 0
        self.now = 0.0
        self.advances = []
        self.budgets = []

    async def read(self, remaining):
        self.budgets.append(remaining)
        snapshots = self.pages[self.page]
        snapshot = snapshots[min(self.reads, len(snapshots) - 1)]
        self.reads += 1
        return snapshot

    async def advance(self, page_number, row_count):
        self.advances.append((page_number, row_count))
        self.page += 1
        self.reads = 0

    async def is_terminal(self, remaining):
        return "next_button_disabled" if self.page == self.terminal else None

    async def pause(self, seconds):
        self.now += seconds

    async def collect(self, max_pages=10, allow_navigation_return=False):
        return await collect_paginated_pages(
            self.read, self.advance, self.is_terminal, self.pause,
            max_pages=max_pages, timeout_seconds=2, target_selector='.row', clock=lambda: self.now,
            allow_navigation_return=allow_navigation_return,
        )


async def test_stale_busy_and_partial_snapshots_do_not_trigger_extra_clicks():
    first = PaginationPage(['first', 'old middle', 'last'])
    partial = PaginationPage(['first', 'partial middle', 'last'])
    second = PaginationPage(['first', 'new middle', 'last'])
    source = PageSource([
        [first],
        [first, PaginationPage(first.rows, ready=False), partial, second, second],
    ], terminal=1)
    result = await source.collect()
    assert result.rows == first.rows + second.rows
    assert result.per_page_counts == [3, 3]
    assert source.advances == [(1, 3)]


async def test_equal_rows_on_different_urls_are_not_silently_dropped():
    source = PageSource([
        [PaginationPage(['same'], url='https://example.test/?p=1')],
        [PaginationPage(['same'], url='https://example.test/?p=2')],
    ], terminal=1)
    result = await source.collect()
    assert result.rows == ['same', 'same']
    assert result.per_page_counts == [1, 1]


async def test_nonadvancing_page_fails_without_reclicking():
    same = PaginationPage(['same'])
    source = PageSource([[same], [same]])
    with pytest.raises(ValueError, match='分页未确认'):
        await source.collect()
    assert source.advances == [(1, 1)]
    assert 0 < min(source.budgets) <= 0.3


async def test_return_to_a_collected_page_is_a_failure():
    first = PaginationPage(['one'], url='https://example.test/1')
    source = PageSource([[first], [PaginationPage(['two'], url='https://example.test/2')], [first]])
    with pytest.raises(ValueError, match='分页出现循环'):
        await source.collect()
    assert source.advances == [(1, 1), (2, 1)]


async def test_empty_mid_sweep_is_not_a_terminal_page():
    source = PageSource([[PaginationPage(['one'])], [PaginationPage([])]])
    with pytest.raises(ValueError, match='一行数据都没抽到'):
        await source.collect()
    assert source.advances == [(1, 1)]


async def test_explicit_empty_terminal_preserves_data_and_data_page_count():
    source = PageSource([[PaginationPage(['one'])], [PaginationPage([])]], terminal=1)
    result = await source.collect()
    assert result.rows == ['one']
    assert result.per_page_counts == [1]


async def test_terminal_marker_does_not_override_busy_content():
    source = PageSource([[PaginationPage(['unfinished'], ready=False)]], terminal=0)
    with pytest.raises(ValueError, match='分页未确认'):
        await source.collect()
    assert source.advances == []


async def test_safety_cap_is_failure_and_does_not_advance_beyond_the_cap():
    source = PageSource([[PaginationPage(['one'])], [PaginationPage(['two'])]])
    with pytest.raises(ValueError, match='安全上限 2 页'):
        await source.collect(max_pages=2)
    assert source.advances == [(1, 1)]


async def test_terminal_on_the_cap_is_complete():
    source = PageSource([[PaginationPage(['one'])], [PaginationPage(['two'])]], terminal=1)
    result = await source.collect(max_pages=2)
    assert result.rows == ['one', 'two']
    assert source.advances == [(1, 1)]


def test_pagination_must_not_suppress_failed_collection():
    with pytest.raises(ValueError, match='continueOnError'):
        pagination_max_pages({'continueOnError': True}, lambda text: text)


@pytest.mark.parametrize('value', [True, False, 0, -1, 1.5, '1.5', '${var.missing}', None])
def test_invalid_safety_cap_never_falls_back_silently(value):
    with pytest.raises(ValueError):
        pagination_max_pages({'maxIterations': value}, lambda text: text)


def test_safety_cap_resolves_variables_and_has_one_default():
    assert pagination_max_pages({}, lambda text: text) == 20
    assert pagination_max_pages({'maxIterations': '${var.limit}'}, lambda text: '3') == 3


async def test_verified_url_return_does_not_duplicate_the_last_data_page():
    first = PaginationPage(['one'], url='https://example.test/?p=1')
    second = PaginationPage(['two'], url='https://example.test/?p=2')
    source = PageSource([[first], [second], [second]])

    async def terminal(remaining):
        return 'navigation_returned_to_previous_page' if source.page == 2 else None

    source.is_terminal = terminal
    result = await source.collect(allow_navigation_return=True)
    assert result.rows == ['one', 'two']
    assert result.per_page_counts == [1, 1]
    assert result.stop_reason == 'navigation_returned_to_previous_page'


async def test_url_return_without_any_forward_progress_is_not_proof_of_completion():
    first = PaginationPage(['one'], url='https://example.test/?p=1')
    source = PageSource([[first], [first]])

    async def terminal(remaining):
        return 'navigation_returned_to_previous_page' if source.page == 1 else None

    source.is_terminal = terminal
    with pytest.raises(ValueError, match='尚无足够翻页进展'):
        await source.collect(allow_navigation_return=True)
