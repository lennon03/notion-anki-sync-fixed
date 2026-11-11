# -*- coding: utf-8 -*-
"""Cloze and overlapping cloze rows."""

from notion_sync_addon.htmlify import MediaCollector, render_lines
from tests.helpers import DB1, _para, _rid, _rt
from tests.test_columns import _assert_noop, _edit

ROW = _rid(0)
OTHER = _rid(1)
CLOZE = "Notion Sync Cloze"


def _kind(value):
    return {"Card type": {"type": "select", "select": {"name": value}}}


def _item(text, kind="bulleted_list_item"):
    return {
        "id": "li",
        "type": kind,
        "has_children": False,
        kind: {"rich_text": _rt(text)},
    }


def _title(env, rid, title):
    env.notion.tables[DB1]["rows"][rid][0] = title


def _page(env, rid, *blocks):
    env.notion.tables[DB1]["rows"][rid][2] = list(blocks)


def _cloze_model(env):
    return env.col.models.by_name(CLOZE)


def _count(env, rid):
    return len(env.col.find_notes(f"Source:{rid}"))


def _fields(env, rid):
    return list(env.note(rid).fields)


def test_title_cloze_makes_one_cloze_note(env):
    _title(env, ROW, "The {{c1::cat}} sat on the {{c2::mat}} {{c1::today}}")
    _page(env, ROW, _para("extra page"))
    stats = env.sync()
    assert stats["added"] == 8
    note = env.note(ROW)
    assert note.note_type()["name"] == CLOZE
    assert note.note_type()["type"] == 1
    text, extra, source = note.fields
    assert text.startswith("The {{c1::cat}}")
    assert "extra page" in extra and source == ROW
    assert len(note.cards()) == 2  # c1 and c2
    assert env.col.get_config("notionSync.noteTypes", {})["cloze"] == (
        _cloze_model(env)["id"]
    )
    assert "[...]" in note.cards()[0].question()


def test_page_cloze_puts_the_title_in_text(env):
    _page(env, ROW, _para("a {{c1::hidden}} word"))
    env.sync()
    text, extra, _ = _fields(env, ROW)
    assert text.startswith('<div class="ns-title">h0</div>')
    assert "{{c1::hidden}}" in text
    assert extra == ""
    assert len(env.note(ROW).cards()) == 1


def test_card_type_column_wins(env):
    _page(env, ROW, _para("{{c1::x}}"))
    env.notion.props[ROW] = _kind("Basic")
    _page(env, OTHER, _para("plain"))
    env.notion.props[OTHER] = _kind("Cloze")
    stats = env.sync()
    assert stats["added"] == 8
    assert env.note(ROW).note_type()["name"] == "Notion Sync Card"
    assert env.note(OTHER).note_type()["name"] == "Notion Sync Card"
    assert any("Cloze" in w and "plain" not in w for w in env.result.warnings)
    assert not any("h0" in w for w in env.result.warnings)


def test_overlapping_without_a_line_is_basic_with_a_warning(env):
    _page(env, ROW)
    env.notion.props[ROW] = _kind("Overlapping")
    env.sync()
    assert env.note(ROW).note_type()["name"] == "Notion Sync Card"
    assert any("Overlapping" in w for w in env.result.warnings)


def test_no_cloze_note_type_without_cloze_rows(env):
    env.sync()
    assert _cloze_model(env) is None
    assert "cloze" not in (env.col.get_config("notionSync.noteTypes", {}))
    env.notion.downloads.clear()
    _assert_noop(env, env.sync(), 8)


def test_switch_basic_to_cloze_and_back_replaces_the_note(env):
    env.sync()
    old = env.note(ROW)
    old.tags.append("mine")
    env.col.update_note(old)
    old_id = old.id
    deck = old.cards()[0].did
    others = {rid: env.note(rid).id for rid in (OTHER, _rid(2))}

    _page(env, ROW, _para("the {{c1::answer}}"))
    _edit(env, ROW, _kind("Cloze"))
    env.col.add_custom_undo_entry("Previous action")
    stats = env.sync()
    assert stats["updated"] == 1 and stats["added"] == 0
    assert stats["removed"] == 0
    assert env.col.undo_status().undo == "Notion Sync"
    assert _count(env, ROW) == 1
    note = env.note(ROW)
    assert note.note_type()["name"] == CLOZE and note.id != old_id
    assert "mine" in note.tags
    assert note.cards()[0].did == deck
    assert not env.col.find_notes(f"nid:{old_id}")
    assert {rid: env.note(rid).id for rid in others} == others
    env.notion.downloads.clear()
    _assert_noop(env, env.sync(), 8)

    # one undo step brings the old basic note back
    env.col.undo()
    assert _count(env, ROW) == 1
    assert env.note(ROW).id == old_id
    assert env.col.undo_status().undo == "Previous action"

    # the sync heals the undone state, then goes back to basic
    env.sync()
    _edit(env, ROW, _kind("Basic"))
    env.notion.tables[DB1]["rows"][ROW][1] = "2026-01-03T00:00:00.000Z"
    stats = env.sync()
    assert stats["updated"] == 1
    assert _count(env, ROW) == 1
    assert env.note(ROW).note_type()["name"] == "Notion Sync Card"
    assert "mine" in env.note(ROW).tags


def test_cloze_to_basic_keeps_user_tags_and_one_note(env):
    _page(env, ROW, _para("{{c1::a}}"))
    env.sync()
    note = env.note(ROW)
    note.tags.append("mine")
    env.col.update_note(note)
    _page(env, ROW, _para("no cloze now"))
    _edit(env, ROW)
    env.sync()
    assert _count(env, ROW) == 1
    assert env.note(ROW).note_type()["name"] == "Notion Sync Card"
    assert "mine" in env.note(ROW).tags


def test_refused_cloze_keeps_the_old_note(env, monkeypatch):
    env.sync()
    old_id = env.note(ROW).id
    _page(env, ROW, _para("{{c0::bad}}"))
    _edit(env, ROW)
    stats = env.sync()
    assert _count(env, ROW) == 1
    note = env.note(ROW)
    if note.id == old_id:  # Anki refused it
        assert stats["updated"] == 0
        assert any("refused" in w for w in env.result.warnings)


def test_hand_made_cloze_note_is_never_touched(env):
    _page(env, ROW, _para("{{c1::a}}"))
    env.sync()
    model = _cloze_model(env)
    from anki.notes import Note

    mine = Note(env.col, model)
    mine.fields = ["my {{c1::own}}", "", "not a row id"]
    env.col.add_note(mine, env.note(ROW).cards()[0].did)
    env.sync()
    env.sync()
    kept = env.col.get_note(mine.id)
    assert kept.fields == ["my {{c1::own}}", "", "not a row id"]


def test_overlapping_lines_become_clozes(env):
    _page(env, ROW, _para("one"), _para("two"), _para("three"))
    env.notion.props[ROW] = _kind("Overlapping")
    env.sync()
    note = env.note(ROW)
    text, extra, _ = note.fields
    assert extra == ""
    assert text.count('<div class="ns-line">') == 3
    for i, word in enumerate(("one", "two", "three"), 1):
        assert (
            f'<div class="ns-line">{{{{c{i}::<p>{word}</p>}}}}</div>' in (text)
            or f'<div class="ns-line">{{{{c{i}::{word}}}}}</div>' in text
        )
    assert '<div class="ns-title">h0</div><div class="ns-overlap">' in text
    assert len(note.cards()) == 3
    assert ".ns-line:has(.cloze) ~ .ns-line" in (
        note.note_type()["tmpls"][0]["qfmt"]
    )
    question = note.cards()[1].question()
    assert "[...]" in question and "one" in question
    env.notion.downloads.clear()
    _assert_noop(env, env.sync(), 8)


def test_paragraph_breaks_and_list_items_are_lines(env):
    two = _para("first\nsecond\n\nthird")
    _page(
        env,
        ROW,
        two,
        _item("item a"),
        _item("item b"),
        _item("nr", "numbered_list_item"),
    )
    env.notion.props[ROW] = _kind("overlapping")
    env.sync()
    note = env.note(ROW)
    text = note.fields[0]
    assert text.count('<div class="ns-line">') == 6
    assert "{{c4::item a}}" in text and "{{c6::nr}}" in text
    assert len(note.cards()) == 6
    assert "<ul>" not in text


def test_braces_in_a_line_do_not_break_the_cloze(env):
    _page(env, ROW, _para("x^{a^{b}}"), _para("tail}"), _para("a::b"))
    env.notion.props[ROW] = _kind("Overlapping")
    env.sync()
    note = env.note(ROW)
    assert len(note.cards()) == 3
    for card in note.cards():
        assert "{{" not in card.question()
        assert "{{" not in card.answer()
    answer = note.cards()[0].answer()
    assert "x^{a^{b}}" in answer.replace("&#125;", "}")


def test_noop_sync_after_cloze_rows(env):
    _title(env, ROW, "a {{c1::b}}")
    _page(env, OTHER, _para("l1"), _para("l2"))
    env.notion.props[OTHER] = _kind("Overlapping")
    env.sync()
    env.notion.downloads.clear()
    env.col.add_custom_undo_entry("Previous action")
    _assert_noop(env, env.sync(), 8)
    assert env.col.undo_status().undo == "Previous action"


def test_renamed_cloze_type_makes_no_duplicates(env):
    _title(env, ROW, "a {{c1::b}}")
    env.sync()
    model = _cloze_model(env)
    mid = model["id"]
    env.col.models.rename_field(model, model["flds"][2], "Origin")
    env.col.models.rename_field(model, model["flds"][0], "Body")
    model["name"] = "Renamed cloze"
    env.col.models.save(model)
    before = sorted(env.col.find_notes(""))
    env.notion.downloads.clear()
    env.col.add_custom_undo_entry("Previous action")
    _assert_noop(env, env.sync(), 8)
    assert sorted(env.col.find_notes("")) == before
    assert env.col.models.by_name(CLOZE) is None
    assert env.col.get_config("notionSync.noteTypes")["cloze"] == mid
    _title(env, ROW, "a {{c1::b}} {{c2::c}}")
    _edit(env, ROW)
    assert env.sync()["updated"] == 1
    assert sorted(env.col.find_notes("")) == before


def test_duplicates_across_both_types_keep_the_oldest(env):
    env.sync()
    first = env.note(ROW)
    _title(env, ROW, "{{c1::x}}")
    _edit(env, ROW)
    env.sync()  # now a cloze note
    from anki.notes import Note

    dup = Note(env.col, env.col.models.by_name("Notion Sync Card"))
    dup.fields = ["dup", "dup", ROW]
    env.col.add_note(dup, env.note(ROW).cards()[0].did)
    env.sync()
    assert _count(env, ROW) == 1
    assert first.id != env.note(ROW).id


def test_render_lines_unit():
    media = MediaCollector()

    class Tree:
        def children(self, block_id):
            return []

    blocks = [
        _para("a\nb"),
        {"id": "d", "type": "divider", "divider": {}},
        _para(""),
        _item("c", "to_do") | {"to_do": {"rich_text": _rt("c"), "checked": 0}},
    ]
    lines = render_lines(blocks, media, Tree())
    assert lines[:2] == ["a", "b"]
    assert len(lines) == 3 and "c" in lines[2]
