# -*- coding: utf-8 -*-
"""Sub-pages of a configured page are read too, as subdecks."""

from tests.helpers import OLD, P1, _para, _rid
from tests.test_page_cards import _h, _p, _rts

SUB, DEEP = "5" * 32, "6" * 32
DB9 = "9" * 32


def _link(page_id, title):
    return {
        "id": page_id,
        "type": "child_page",
        "has_children": False,
        "child_page": {"title": title},
    }


def _sub(env, parent, page_id, title, *blocks, tables=()):
    """Put a sub-page with ``blocks`` under ``parent``."""
    env.notion.pages[page_id] = {
        "edited": OLD,
        "title": title,
        "tables": list(tables),
    }
    env.notion.page_blocks[page_id] = list(blocks)
    env.notion.page_blocks.setdefault(parent, []).append(_link(page_id, title))


def _touch(env, page_id, when="2026-01-03T00:00:00.000Z"):
    env.notion.pages[page_id]["edited"] = when


def _deck(env, source):
    return env.col.decks.name(env.note(source).cards()[0].did)


def _blk(n):
    return f"b{n:03d}".ljust(32, "0")


def test_a_sub_page_is_a_subdeck_and_so_is_a_page_under_it(env):
    _sub(env, P1, SUB, "Chapter 1", _h(3, "Why?", 1), _p("Because.", 2))
    _sub(env, SUB, DEEP, "Part A", _p("x :: y", 3))
    stats = env.sync()
    assert stats["added"] == 8 + 2
    assert _deck(env, _blk(1)) == "Hist::Chapter 1"
    assert _deck(env, _blk(3)) == "Hist::Chapter 1::Part A"


def test_a_table_on_a_sub_page_is_a_subdeck_of_it(env):
    env.notion.tables[DB9] = {
        "title": "Dates",
        "rows": {_rid(90): ["1789", OLD, [_para("Bastille")]]},
    }
    _sub(env, P1, SUB, "Chapter 1", tables=[DB9])
    env.sync()
    assert _deck(env, _rid(90)) == "Hist::Chapter 1::Dates"


def test_an_edit_in_a_sub_page_is_seen_while_its_parent_is_unchanged(env):
    _sub(env, P1, SUB, "Chapter 1", _h(3, "Why?", 1), _p("Because.", 2))
    env.sync()
    env.notion.calls.clear()
    stats = env.sync()
    assert stats["added"] == stats["updated"] == stats["removed"] == 0
    assert ("blocks", P1) not in env.notion.calls
    assert ("blocks", SUB) not in env.notion.calls

    env.notion.page_blocks[SUB][1]["paragraph"]["rich_text"] = _rts("New.")
    _touch(env, SUB)
    stats = env.sync()
    assert stats["updated"] == 1
    assert "New." in env.note(_blk(1)).fields[1]
    assert ("blocks", P1) not in env.notion.calls


def test_removing_a_sub_page_removes_its_cards(env):
    _sub(env, P1, SUB, "Chapter 1", _h(3, "Why?", 1), _p("Because.", 2))
    env.sync()
    env.notion.page_blocks[P1] = []
    _touch(env, P1)
    stats = env.sync()
    assert stats["removed"] == 1 and env.note(_blk(1)) is None


def test_a_sub_page_that_cannot_be_read_keeps_its_cards(env):
    _sub(env, P1, SUB, "Chapter 1", _h(3, "Why?", 1), _p("Because.", 2))
    env.sync()
    env.notion.fail = {("page", SUB)}
    stats = env.sync()
    assert stats["removed"] == 0 and env.note(_blk(1)) is not None
    assert any("Sub-page of 'Hist'" in p for p in env.result.problems)


def test_renaming_a_sub_page_moves_its_cards(env):
    _sub(env, P1, SUB, "Chapter 1", _h(3, "Why?", 1), _p("Because.", 2))
    env.sync()
    env.notion.pages[SUB]["title"] = "Chapter one"
    _touch(env, SUB)
    env.sync()
    assert _deck(env, _blk(1)) == "Hist::Chapter one"


def test_sub_pages_can_be_turned_off(env):
    _sub(env, P1, SUB, "Chapter 1", _h(3, "Why?", 1), _p("Because.", 2))
    env.cfg["sub_pages"] = False
    stats = env.sync()
    assert stats["added"] == 8 and env.note(_blk(1)) is None


def test_a_sub_page_listed_in_the_settings_too_is_read_once(env):
    _sub(env, P1, SUB, "Chapter 1", _h(3, "Why?", 1), _p("Because.", 2))
    env.cfg["notion_pages"] = [P1, SUB]
    stats = env.sync()
    assert stats["added"] == 3 + 1  # P1's table and the card, once
    assert len(env.col.find_notes(f"Source:{_blk(1)}")) == 1


def test_a_cache_from_before_sub_pages_finds_them_on_the_next_sync(env):
    env.sync()
    # the page gets a sub-page but Notion reports no edit of the page
    # itself (as for a cache written before sub-pages were read)
    import json

    with open(env.path, encoding="utf-8") as f:
        state = json.load(f)
    state["pages"][P1] = state["pages"][P1][:4]
    with open(env.path, "w", encoding="utf-8") as f:
        json.dump(state, f)
    env.notion.pages[SUB] = {"edited": OLD, "title": "Late", "tables": []}
    env.notion.page_blocks[SUB] = [_p("a :: b", 7)]
    env.notion.page_blocks[P1] = [_link(SUB, "Late")]
    stats = env.sync()
    assert stats["added"] == 1 and _deck(env, _blk(7)) == "Hist::Late"


def test_a_sub_page_link_is_not_part_of_the_answer_above_it(env):
    env.notion.page_blocks[P1] = [_h(3, "Why?", 1), _p("Because.", 2)]
    _sub(env, P1, SUB, "Chapter 1", _p("a :: b", 3))
    env.sync()
    assert env.note(_blk(1)).fields[1] == "<p>Because.</p>"
