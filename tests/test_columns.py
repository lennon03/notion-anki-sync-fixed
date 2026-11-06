# -*- coding: utf-8 -*-
"""Row properties: tags, the sync checkbox, and the note type registry."""

import hashlib

from notion_sync_addon.sync import (
    TAG_PREFIX,
    UNCHECKED_TAG,
    RowColumns,
    clean_tag,
    fingerprint,
    row_columns,
)
from tests.test_htmlify_and_sync import DB1, _para, _rid

EDITED = "2026-01-02T00:00:00.000Z"
ROW = _rid(0)
MARKER = UNCHECKED_TAG


def _rt(text, **extra):
    return {"plain_text": text, **extra}


def _row(props):
    return {"id": "r", "properties": props}


# --------------------------------------------------------------------- #
# row_columns
# --------------------------------------------------------------------- #


def test_row_columns_plain_text_of_every_property_type():
    props = {
        "Name": {"type": "title", "title": [_rt("Ti"), _rt("tle")]},
        "Text": {"type": "rich_text", "rich_text": [_rt("a"), _rt("b")]},
        "Sel": {"type": "select", "select": {"name": "One"}},
        "NoSel": {"type": "select", "select": None},
        "Stat": {"type": "status", "status": {"name": "Done"}},
        "Multi": {
            "type": "multi_select",
            "multi_select": [{"name": "x"}, {"name": "y"}],
        },
        "Num": {"type": "number", "number": 4.5},
        "NoNum": {"type": "number", "number": None},
        "Yes": {"type": "checkbox", "checkbox": True},
        "No": {"type": "checkbox", "checkbox": False},
        "Day": {"type": "date", "date": {"start": "2026-01-01", "end": None}},
        "Span": {
            "type": "date",
            "date": {"start": "2026-01-01", "end": "2026-02-01"},
        },
        "Link": {"type": "url", "url": "https://x.org"},
        "Mail": {"type": "email", "email": "a@b.c"},
        "Tel": {"type": "phone_number", "phone_number": "123"},
        "FStr": {
            "type": "formula",
            "formula": {"type": "string", "string": "s"},
        },
        "FNum": {
            "type": "formula",
            "formula": {"type": "number", "number": 2},
        },
        "FBool": {
            "type": "formula",
            "formula": {"type": "boolean", "boolean": True},
        },
        "FDate": {
            "type": "formula",
            "formula": {"type": "date", "date": {"start": "2026-03-03"}},
        },
        "Rel": {"type": "relation", "relation": [{"id": "ab-cd"}]},
        "Who": {
            "type": "people",
            "people": [{"name": "Ann"}, {"name": "Bob"}],
        },
        "Files": {"type": "files", "files": []},
    }
    got = row_columns(_row(props), {}).props
    assert got == {
        "Name": "Title",
        "Text": "ab",
        "Sel": "One",
        "NoSel": "",
        "Stat": "Done",
        "Multi": "x, y",
        "Num": "4.5",
        "NoNum": "",
        "Yes": "Yes",
        "No": "",
        "Day": "2026-01-01",
        "Span": "2026-01-01 → 2026-02-01",
        "Link": "https://x.org",
        "Mail": "a@b.c",
        "Tel": "123",
        "FStr": "s",
        "FNum": "2",
        "FBool": "Yes",
        "FDate": "2026-03-03",
        "Rel": "",
        "Who": "Ann, Bob",
        "Files": "",
    }


def test_row_columns_special_columns():
    props = {
        " anki ": {"type": "checkbox", "checkbox": False},
        "TAGS": {
            "type": "multi_select",
            "multi_select": [{"name": "A b"}, {"name": "a  B"}, {"name": ""}],
        },
        "card TYPE": {"type": "select", "select": {"name": "Cloze"}},
        "context": {"type": "relation", "relation": [{"id": "ab-cd-ef"}]},
    }
    cols = row_columns(_row(props), {})
    assert cols.sync is False
    assert cols.tags == ["A_b"]
    assert cols.card_type == "cloze"
    assert cols.context == ["abcdef"]


def test_row_columns_no_columns_and_disabled_columns():
    props = {
        "Anki": {"type": "checkbox", "checkbox": False},
        "Tags": {"type": "multi_select", "multi_select": [{"name": "t"}]},
        "Card type": {"type": "select", "select": {"name": "Cloze"}},
        "Context": {"type": "url", "url": "https://x.org"},
    }
    assert row_columns(_row({}), {}) == RowColumns()
    off = {
        "sync_column": "",
        "tags_column": "",
        "card_type_column": "",
        "context_column": "",
    }
    cols = row_columns(_row(props), off)
    assert (cols.sync, cols.tags, cols.card_type, cols.context) == (
        None,
        [],
        "",
        [],
    )
    renamed = {"sync_column": "Export", "tags_column": "Labels"}
    cols = row_columns(_row(props), renamed)
    assert cols.sync is None and cols.tags == []
    props["Export"] = {"type": "checkbox", "checkbox": True}
    assert row_columns(_row(props), renamed).sync is True


def test_row_columns_sync_formula_and_wrong_types():
    formula = {"type": "formula", "formula": {"type": "boolean", "boolean": 0}}
    assert row_columns(_row({"Anki": formula}), {}).sync is False
    text = {"type": "rich_text", "rich_text": [_rt("no")]}
    assert row_columns(_row({"Anki": text}), {}).sync is None


def test_row_columns_tag_sources_and_context_sources():
    sel = {"Tags": {"type": "select", "select": {"name": "one two"}}}
    assert row_columns(_row(sel), {}).tags == ["one_two"]
    text = {
        "Tags": {"type": "rich_text", "rich_text": [_rt("a, b c ,,A")]},
    }
    assert row_columns(_row(text), {}).tags == ["a", "b_c"]
    url = {"Context": {"type": "url", "url": "https://x.org/p"}}
    assert row_columns(_row(url), {}).context == ["https://x.org/p"]
    rich = {
        "Context": {
            "type": "rich_text",
            "rich_text": [
                _rt("see", href="https://y.org"),
                _rt(
                    "page",
                    type="mention",
                    mention={"type": "page", "page": {"id": "12-34"}},
                    href="https://notion.so/1234",
                ),
                _rt("plain"),
            ],
        }
    }
    assert row_columns(_row(rich), {}).context == ["https://y.org", "1234"]


def test_clean_tag():
    assert clean_tag("  big  idea ") == "big_idea"
    assert clean_tag('a"b') == "ab"
    assert clean_tag(":a::b:") == "a::b"
    assert clean_tag("a::::b") == "a::b"
    assert clean_tag(' " ') == ""


# --------------------------------------------------------------------- #
# fingerprint
# --------------------------------------------------------------------- #


def test_fingerprint_without_tags_is_the_old_formula():
    def old(front, back):
        return hashlib.sha1(f"{front}\x1f{back}".encode("utf-8")).hexdigest()[
            :16
        ]

    assert fingerprint("f", "b") == old("f", "b")
    assert fingerprint("f", "b", tags="") == old("f", "b")
    assert fingerprint("f", "b", tags="notion::x") != old("f", "b")


# --------------------------------------------------------------------- #
# Tags
# --------------------------------------------------------------------- #


def _tags(names):
    return {
        "Tags": {
            "type": "multi_select",
            "multi_select": [{"name": n} for n in names],
        }
    }


def _edit(env, rid, props=None, db=DB1):
    """Edit a row in Notion: new properties and a newer edit time."""
    env.notion.tables[db]["rows"][rid][1] = EDITED
    if props is not None:
        env.notion.props[rid] = props


def _assert_noop(env, stats, count):
    assert stats["unchanged"] == count
    assert stats["added"] == stats["updated"] == stats["removed"] == 0
    assert env.notion.downloads == []
    assert env.result.changes is None


def test_tags_are_added_cleaned_updated_and_removed(env):
    env.notion.props[ROW] = _tags(["Big Idea", 'q"x', "a::b", "big  idea"])
    env.sync()
    note = env.note(ROW)
    assert sorted(note.tags) == [
        "Notion::Big_Idea",
        "Notion::a::b",
        "Notion::qx",
    ]
    note.tags.append("mine")
    env.col.update_note(note)

    env.notion.downloads.clear()
    env.col.add_custom_undo_entry("Previous action")
    stats = env.sync()
    _assert_noop(env, stats, 8)
    assert env.col.undo_status().undo == "Previous action"

    _edit(env, ROW, _tags(["Other"]))
    assert env.sync()["updated"] == 1
    assert sorted(env.note(ROW).tags) == ["Notion::Other", "mine"]

    _edit(env, ROW, _tags([]))
    assert env.sync()["updated"] == 1
    assert env.note(ROW).tags == ["mine"]
    env.notion.downloads.clear()
    _assert_noop(env, env.sync(), 8)


def test_tags_changed_without_edit_time_need_no_download(env):
    env.sync()
    env.notion.props[ROW] = _tags(["late"])
    env.notion.downloads.clear()
    stats = env.sync()
    assert stats["updated"] == 1 and env.notion.downloads == []
    assert env.note(ROW).tags == ["Notion::late"]
    _assert_noop(env, env.sync(), 8)


def test_hand_added_tags_survive_and_owned_tags_match_any_case(env):
    env.sync()
    note = env.note(ROW)
    note.tags = ["keep", "notion::stray"]
    env.col.update_note(note)
    assert env.sync()["updated"] == 1  # the stray owned tag goes
    assert env.note(ROW).tags == ["keep"]
    env.notion.downloads.clear()
    _assert_noop(env, env.sync(), 8)


def test_tagged_rows_in_the_listing_get_row_card_tags(env):
    env.notion.props[ROW] = _tags(["x"])
    env.sync()
    cards = {
        row.source_id: row
        for page in env.result.pages
        for table in page.tables
        for row in table.rows
    }
    assert cards[ROW].tags == ["Notion::x"]
    assert cards[_rid(1)].tags == []
    env.sync()  # unchanged rows carry their tags too
    cards = {
        row.source_id: row
        for page in env.result.pages
        for table in page.tables
        for row in table.rows
    }
    assert cards[ROW].unchanged and cards[ROW].tags == ["Notion::x"]
    assert TAG_PREFIX == "Notion::"


# --------------------------------------------------------------------- #
# Note type registry
# --------------------------------------------------------------------- #

KEY = "notionSync.noteTypes"


def test_registry_written_in_the_first_sync_undo_step(env):
    env.col.add_custom_undo_entry("Previous action")
    env.sync()
    mid = env.col.models.by_name("Notion Sync Card")["id"]
    assert env.col.get_config(KEY, None) == {"basic": mid}
    assert env.sync_mod.own_model_ids(env.col) == {"basic": mid}
    assert env.col.undo_status().undo == "Notion Sync"
    env.col.undo()
    assert env.col.get_config(KEY, None) is None
    assert env.col.undo_status().undo == "Previous action"


def test_renamed_note_type_and_source_field_make_no_duplicates(env):
    env.sync()
    model = env.col.models.by_name("Notion Sync Card")
    mid = model["id"]
    env.col.models.rename_field(model, model["flds"][2], "Origin")
    model["name"] = "My own name"
    env.col.models.save(model)
    notes_before = sorted(env.col.find_notes(""))

    env.col.add_custom_undo_entry("Previous action")
    env.notion.downloads.clear()
    stats = env.sync()
    _assert_noop(env, stats, 8)
    assert env.col.undo_status().undo == "Previous action"
    assert sorted(env.col.find_notes("")) == notes_before
    assert env.col.get_config(KEY, None) == {"basic": mid}

    # edits still update the renamed type's notes, by position
    env.notion.tables[DB1]["rows"][ROW][2] = [_para("new text")]
    _edit(env, ROW)
    stats = env.sync()
    assert stats["updated"] == 1 and stats["added"] == 0
    entry = env.sync_mod.synced_notes(env.col)[ROW][0]
    assert "new text" in env.col.get_note(entry.nid).fields[1]
    assert sorted(env.col.find_notes("")) == notes_before


def test_stale_registry_id_falls_back_to_the_name_and_keeps_other_roles(env):
    env.sync()
    env.col.set_config(KEY, {"basic": 123, "cloze": 456})
    assert env.sync_mod.own_model_ids(env.col) == {
        "basic": env.col.models.by_name("Notion Sync Card")["id"]
    }
    env.col.add_custom_undo_entry("Previous action")
    stats = env.sync()
    mid = env.col.models.by_name("Notion Sync Card")["id"]
    assert stats["unchanged"] == 8 and stats["added"] == 0
    assert env.col.get_config(KEY, None) == {"basic": mid, "cloze": 456}
    assert env.col.undo_status().undo == "Notion Sync"


def test_synced_notes_shape(env):
    env.notion.props[ROW] = _tags(["x"])
    env.sync()
    notes = env.sync_mod.synced_notes(env.col)
    assert len(notes) == 8
    entry = notes[ROW][0]
    assert entry.mid == env.col.models.by_name("Notion Sync Card")["id"]
    assert "Notion::x" in entry.tags
    assert entry.nid == env.note(ROW).id


# --------------------------------------------------------------------- #
# Checkbox column
# --------------------------------------------------------------------- #


def _check(value):
    return {"Anki": {"type": "checkbox", "checkbox": value}}


def _suspended(env, rid):
    return [c.queue == -1 for c in env.note(rid).cards()]


def test_unchecked_new_row_gets_no_card_and_no_download(env):
    env.notion.props[ROW] = _check(False)
    stats = env.sync()
    assert stats["added"] == 7 and env.note(ROW) is None
    assert ROW not in env.notion.downloads
    env.notion.downloads.clear()
    _assert_noop(env, env.sync(), 7)
    # the origin is kept for removal scoping
    assert env.sync_mod.load_cache(env.path).rows[ROW].db_id == DB1


def test_unchecking_a_synced_row_suspends_it_once(env):
    env.sync()
    env.notion.props[ROW] = _check(False)
    env.notion.downloads.clear()
    env.col.add_custom_undo_entry("Previous action")
    stats = env.sync()
    assert stats["updated"] == 1 and stats["removed"] == 0
    assert env.notion.downloads == []
    assert env.col.undo_status().undo == "Notion Sync"
    note = env.note(ROW)
    assert MARKER in note.tags and all(_suspended(env, ROW))

    _assert_noop(env, env.sync(), 8)

    env.col.undo_status()
    env.col.undo()
    note = env.note(ROW)
    assert MARKER not in note.tags and not any(_suspended(env, ROW))


def test_manual_unsuspend_of_an_unchecked_row_is_respected(env):
    env.sync()
    env.notion.props[ROW] = _check(False)
    env.sync()
    cids = [c.id for c in env.note(ROW).cards()]
    env.col.sched.unsuspend_cards(cids)
    env.notion.downloads.clear()
    _assert_noop(env, env.sync(), 8)
    assert not any(_suspended(env, ROW))


def test_checking_again_unsuspends_and_removes_the_marker(env):
    env.sync()
    env.notion.props[ROW] = _check(False)
    env.sync()
    env.notion.props[ROW] = _check(True)
    _edit(env, ROW)
    env.col.add_custom_undo_entry("Previous action")
    stats = env.sync()
    assert stats["updated"] == 1 and stats["removed"] == 0
    assert env.col.undo_status().undo == "Notion Sync"
    note = env.note(ROW)
    assert MARKER not in note.tags and not any(_suspended(env, ROW))
    env.notion.downloads.clear()
    _assert_noop(env, env.sync(), 8)


def test_removing_the_column_releases_the_cards(env):
    env.sync()
    env.notion.props[ROW] = _check(False)
    env.sync()
    del env.notion.props[ROW]
    stats = env.sync()
    assert stats["removed"] == 0
    assert MARKER not in env.note(ROW).tags
    assert not any(_suspended(env, ROW))
    env.notion.downloads.clear()
    _assert_noop(env, env.sync(), 8)


def test_hand_suspended_cards_of_a_checked_row_stay_suspended(env):
    for rid in env.notion.tables[DB1]["rows"]:
        env.notion.props[rid] = _check(True)
    env.sync()
    env.col.sched.suspend_cards([c.id for c in env.note(ROW).cards()])
    env.notion.downloads.clear()
    _assert_noop(env, env.sync(), 8)
    assert all(_suspended(env, ROW)) and MARKER not in env.note(ROW).tags


def test_tables_without_the_column_and_a_disabled_column_sync_all(env):
    env.notion.props[ROW] = _check(False)
    env.cfg["sync_column"] = ""
    assert env.sync()["added"] == 8
    env.cfg["sync_column"] = "Other"
    assert env.sync()["unchanged"] == 8


def test_unchecked_row_deleted_in_notion_is_removed(env):
    env.sync()
    env.notion.props[ROW] = _check(False)
    env.sync()
    del env.notion.tables[DB1]["rows"][ROW]
    del env.notion.props[ROW]
    stats = env.sync()
    assert stats["removed"] == 1 and env.note(ROW) is None


def test_unchecked_row_is_never_removed_while_it_exists(env):
    env.sync()
    env.notion.props[ROW] = _check(False)
    env.sync()
    for _ in range(2):
        assert env.sync()["removed"] == 0
    assert env.note(ROW) is not None
