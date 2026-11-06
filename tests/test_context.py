# -*- coding: utf-8 -*-
"""The Context column: linked pages shown after the card's Back."""

import time

import pytest

from notion_sync_addon.notion_api import NotionError
from notion_sync_addon.sync import (
    CONTEXT_MAX_AGE_SECONDS,
    VOLATILE_MAX_AGE_SECONDS,
    RowState,
    is_unchanged,
)
from tests.test_htmlify_and_sync import (
    DB1,
    EDITED,
    EDITED_TS,
    _image,
    _mention,
    _para,
    _rid,
)

ROW = _rid(0)
CTX = "c" * 32
CTX2 = "e" * 32


def _relation(*ids):
    return {
        "Context": {"type": "relation", "relation": [{"id": i} for i in ids]}
    }


def _mention_block():
    return {
        "type": "paragraph",
        "has_children": False,
        "paragraph": {"rich_text": [_mention()]},
    }


def _url(value):
    return {"Context": {"type": "url", "url": value}}


@pytest.fixture
def ctx(env):
    """``env`` whose fake Notion also serves context pages."""
    notion = env.notion
    notion.ctx = {CTX: ("Background", [_para("linked text")])}
    notion.ctx_errors = {}  # page id -> NotionError
    notion.ctx_requests = []  # ("page"|"blocks", id)
    plain_get_page, plain_children = notion.get_page, notion.children

    def get_page(pid):
        if pid in notion.ctx or pid in notion.ctx_errors:
            notion.calls.append(("page", pid))
            notion.ctx_requests.append(("page", pid))
            if pid in notion.ctx_errors:
                raise notion.ctx_errors[pid]
            title = notion.ctx[pid][0]
            return {
                "properties": {
                    "t": {"type": "title", "title": [{"plain_text": title}]}
                }
            }
        return plain_get_page(pid)

    def children(bid):
        if bid in notion.ctx:
            notion.ctx_requests.append(("blocks", bid))
            return notion.ctx[bid][1]
        return plain_children(bid)

    notion.get_page, notion.children = get_page, children
    return env


def _back(env, rid=ROW):
    return env.note(rid).fields[1]


def test_relation_context_is_a_details_block_after_the_page(ctx):
    ctx.notion.props[ROW] = _relation(CTX)
    ctx.sync()
    back = _back(ctx)
    assert "hist 0" in back
    assert (
        '<details class="ns-context"><summary>Context: Background</summary>'
        "<p>linked text</p></details>"
    ) in back
    assert back.index("hist 0") < back.index("ns-context")
    # dashed ids from the API work too
    assert ctx.result.fetched[ROW].volatile == 2


def test_title_is_escaped_and_entries_keep_their_order(ctx):
    ctx.notion.ctx[CTX] = ("A <b> & B", [_para("x")])
    ctx.notion.ctx[CTX2] = ("Second", [_para("y")])
    ctx.notion.props[ROW] = {
        **_relation(CTX),
    }
    ctx.notion.props[ROW]["Context"]["relation"].append({"id": CTX2})
    ctx.sync()
    back = _back(ctx)
    assert "Context: A &lt;b&gt; &amp; B" in back
    assert back.index("A &lt;b&gt;") < back.index("Second")


def test_many_rows_sharing_a_context_page_fetch_it_once(ctx):
    for i in range(3):
        ctx.notion.props[_rid(i)] = _relation(CTX)
    ctx.notion.props[_rid(10)] = _relation(CTX)
    ctx.notion.props[_rid(11)] = _relation(CTX)
    ctx.sync()
    assert ctx.notion.ctx_requests.count(("blocks", CTX)) == 1
    assert ctx.notion.ctx_requests.count(("page", CTX)) == 1
    for rid in (_rid(0), _rid(1), _rid(2), _rid(10), _rid(11)):
        assert "linked text" in _back(ctx, rid)


def test_notion_url_is_fetched_and_other_url_is_only_a_link(ctx):
    ctx.notion.props[_rid(0)] = _url(f"https://www.notion.so/Some-Page-{CTX}")
    ctx.notion.props[_rid(1)] = _url("https://example.com/a?b=1&c=2")
    ctx.sync()
    assert "<summary>Context: Background</summary>" in _back(ctx, _rid(0))
    other = _back(ctx, _rid(1))
    assert (
        '<p class="ns-context"><a href="https://example.com/a?b=1&amp;c=2">'
        "Context: https://example.com/a?b=1&amp;c=2</a></p>"
    ) in other
    assert ctx.notion.ctx_requests == [("page", CTX), ("blocks", CTX)]


def test_unshared_context_page_becomes_a_link_and_a_warning(ctx):
    ctx.notion.ctx_errors[CTX] = NotionError("object_not_found", 404)
    ctx.notion.props[_rid(0)] = _relation(CTX)
    ctx.notion.props[_rid(1)] = _relation(CTX)
    stats = ctx.sync()
    assert stats["added"] == 8 and not ctx.result.incomplete
    assert ctx.result.failures == 0
    for rid in (_rid(0), _rid(1)):
        assert (
            '<p class="ns-context"><a href="https://www.notion.so/'
            f'{CTX}">Context</a></p>'
        ) in _back(ctx, rid)
    warned = [w for w in ctx.result.warnings if CTX[:8] in w]
    assert len(warned) == 1
    assert ctx.notion.ctx_requests == [("page", CTX)]


def test_transient_context_error_protects_the_row_and_retries(ctx):
    ctx.sync()
    old = _back(ctx)
    ctx.notion.tables[DB1]["rows"][ROW][1] = EDITED
    ctx.notion.tables[DB1]["rows"][ROW][2] = [_para("changed")]
    ctx.notion.props[ROW] = _relation(CTX)
    ctx.notion.ctx_errors[CTX] = NotionError("503", 503, transient=True)
    stats = ctx.sync()
    assert ctx.result.incomplete and ROW in ctx.result.protected
    assert stats["removed"] == 0 and _back(ctx) == old
    del ctx.notion.ctx_errors[CTX]
    ctx.sync()
    assert "changed" in _back(ctx) and "linked text" in _back(ctx)


def test_second_sync_makes_no_context_request_and_no_undo_step(ctx):
    ctx.notion.props[ROW] = _relation(CTX)
    ctx.sync()
    ctx.notion.ctx_requests.clear()
    ctx.notion.downloads.clear()
    ctx.notion.calls.clear()
    ctx.col.add_custom_undo_entry("Previous action")
    stats = ctx.sync()
    assert stats["unchanged"] == 8 and stats["updated"] == 0
    assert ctx.notion.ctx_requests == []
    assert ctx.notion.downloads == []
    assert ctx.result.changes is None
    assert ctx.col.undo_status().undo == "Previous action"
    assert not [c for c in ctx.notion.calls if c[1] == ROW]


def test_context_row_is_downloaded_again_after_a_day(ctx, monkeypatch):
    ctx.notion.props[ROW] = _relation(CTX)
    ctx.sync()
    ctx.notion.ctx[CTX] = ("Background", [_para("new linked text")])
    real = time.time
    monkeypatch.setattr(
        ctx.sync_mod.time,
        "time",
        lambda: real() + CONTEXT_MAX_AGE_SECONDS - 600,
    )
    ctx.sync()
    assert "new linked text" not in _back(ctx)
    monkeypatch.setattr(
        ctx.sync_mod.time,
        "time",
        lambda: real() + CONTEXT_MAX_AGE_SECONDS + 600,
    )
    ctx.notion.downloads.clear()
    stats = ctx.sync()
    assert ctx.notion.downloads == [ROW]
    assert stats["updated"] == 1
    assert "new linked text" in _back(ctx)


def test_context_page_with_a_mention_makes_the_row_hourly(ctx):
    ctx.notion.ctx[CTX] = ("Background", [_mention_block()])
    ctx.notion.props[ROW] = _relation(CTX)
    ctx.notion.props[_rid(1)] = _relation(CTX)  # memoized page, same level
    ctx.sync()
    assert ctx.result.fetched[ROW].volatile == 1
    assert ctx.result.fetched[_rid(1)].volatile == 1


def test_failed_media_in_the_context_forgets_the_row(ctx):
    ctx.notion.ctx[CTX] = (
        "Background",
        [_image("a" * 32, "pic.png")],
    )
    ctx.notion.props[ROW] = _relation(CTX)
    ctx.notion.media_fail = True
    ctx.sync()
    assert ctx.result.fetched[ROW].edited == ""
    assert ctx.result.fetched[_rid(1)].edited != ""


def test_old_cache_entries_and_round_trip():
    old_false = RowState.from_json(["e", 1.0, "fp", "p", "d", False])
    old_true = RowState.from_json(["e", 1.0, "fp", "p", "d", True])
    assert old_false is not None and old_false.volatile == 0
    assert old_true is not None and old_true.volatile == 1
    zero = RowState.from_json(["e", 1.0, "fp", "p", "d", 0])
    assert zero is not None and zero.volatile == 0
    for level in (0, 1, 2):
        state = RowState("e", 1.5, "fp", "p", "d", level)
        assert RowState.from_json(state.to_json()) == state
        assert state.to_json()[5] == level and len(state.to_json()) == 6


def test_is_unchanged_levels():
    start = EDITED_TS + 600
    hourly = RowState(EDITED, start, volatile=1)
    daily = RowState(EDITED, start, volatile=2)
    plain = RowState(EDITED, start)
    after_hour = start + VOLATILE_MAX_AGE_SECONDS
    assert not is_unchanged(hourly, EDITED, "k", after_hour)
    assert is_unchanged(daily, EDITED, "k", after_hour)
    assert is_unchanged(plain, EDITED, "k", after_hour)
    assert is_unchanged(
        daily, EDITED, "k", start + CONTEXT_MAX_AGE_SECONDS - 1
    )
    assert not is_unchanged(
        daily, EDITED, "k", start + CONTEXT_MAX_AGE_SECONDS
    )
