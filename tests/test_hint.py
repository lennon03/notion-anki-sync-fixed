# -*- coding: utf-8 -*-
"""The "#### Hint" section: a collapsed hint after the card's Back."""

from notion_sync_addon.sync import RowState, split_hint
from tests.test_htmlify_and_sync import DB1, _image, _para, _rid

ROW = _rid(0)


def _heading(text, level=4):
    key = f"heading_{level}"
    return {
        "id": "h",
        "type": key,
        "has_children": False,
        key: {"rich_text": [{"type": "text", "plain_text": text}]},
    }


DIVIDER = {"id": "d", "type": "divider", "has_children": False, "divider": {}}


def _set(env, *blocks):
    env.notion.tables[DB1]["rows"][ROW][2] = list(blocks)


def _back(env):
    return env.note(ROW).fields[1]


def test_split_hint_until_the_divider_and_to_the_end():
    a, b, c, d = (_para(x) for x in "abcd")
    assert split_hint([a, b]) == ([a, b], [])
    assert split_hint([a, _heading("Hint"), b, c]) == ([a], [b, c])
    assert split_hint([a, _heading(" HINT "), b, DIVIDER, c, d]) == (
        [a, c, d],
        [b],
    )
    # only a level 4 heading called Hint counts
    assert split_hint([_heading("Hint", 3), a]) == (
        [_heading("Hint", 3), a],
        [],
    )
    assert split_hint([_heading("Hints"), a]) == ([_heading("Hints"), a], [])


def test_hint_becomes_a_collapsed_section_after_the_back(env):
    _set(
        env,
        _para("front text"),
        _heading("Hint"),
        _para("the hint"),
        DIVIDER,
        _para("more back"),
    )
    env.sync()
    back = _back(env)
    assert (
        '<details class="ns-context"><summary>Hint</summary>'
        "<p>the hint</p></details>"
    ) in back
    assert back.index("more back") < back.index("ns-context")
    assert "front text" in back and "<hr>" not in back
    assert "Hint</h4>" not in back


def test_hint_without_a_divider_runs_to_the_end(env):
    _set(env, _para("body"), _heading("Hint"), _para("one"), _para("two"))
    env.sync()
    back = _back(env)
    assert "<p>one</p>\n<p>two</p></details>" in back


def test_empty_hint_adds_nothing(env):
    _set(env, _para("body"), _heading("Hint"), DIVIDER)
    env.sync()
    assert "ns-context" not in _back(env)


def test_hint_is_not_part_of_an_overlapping_cloze(env):
    env.notion.props[ROW] = {
        "Card type": {"type": "select", "select": {"name": "Overlapping"}}
    }
    _set(env, _para("l1"), _para("l2"), _heading("Hint"), _para("secret"))
    env.sync()
    note = env.note(ROW)
    assert "secret" not in note.fields[0]
    assert "secret" in note.fields[1]


def test_hint_media_is_imported(env):
    _set(env, _heading("Hint"), _image("a" * 32, "pic.png"))
    env.sync()
    assert "<img" in _back(env)


def test_failed_media_in_the_hint_forgets_the_row(env):
    _set(env, _heading("Hint"), _image("a" * 32, "pic.png"))
    env.notion.media_fail = True
    env.sync()
    assert env.result.fetched[ROW].edited == ""
    assert env.result.fetched[_rid(1)].edited != ""


def test_old_cache_entries_and_round_trip():
    old_false = RowState.from_json(["e", 1.0, "fp", "p", "d", False])
    old_true = RowState.from_json(["e", 1.0, "fp", "p", "d", True])
    assert old_false is not None and old_false.volatile == 0
    assert old_true is not None and old_true.volatile == 1
    for level in (0, 1):
        state = RowState("e", 1.5, "fp", "p", "d", level)
        assert RowState.from_json(state.to_json()) == state


def test_hint_rows_are_cached_like_any_other(env):
    _set(env, _para("body"), _heading("Hint"), _para("h"))
    env.sync()
    env.notion.downloads.clear()
    env.sync()
    assert env.notion.downloads == []
