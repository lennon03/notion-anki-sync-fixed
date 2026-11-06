# -*- coding: utf-8 -*-
"""Export a table's new rows to a note type the user chose."""

from tests.test_columns import _assert_noop, _edit
from tests.test_htmlify_and_sync import qapp  # noqa: F401 - fixture
from tests.test_htmlify_and_sync import (
    DB1,
    DB2,
    P1,
    _para,
    _rid,
    canonical_page_id,
)

ROW = _rid(0)
OTHER = _rid(1)
KEY = "notionSync.noteTypes"
HEX = "f" * 32


def _make_type(env, name="Mine", fields=("Q", "A", "Source", "Level")):
    models = env.col.models
    model = models.new(name)
    for fname in fields:
        models.add_field(model, models.new_field(fname))
    models.add_template(
        model,
        {
            "name": "c",
            "qfmt": "{{%s}}" % fields[0],
            "afmt": "{{%s}}" % fields[1],
        },
    )
    models.add_dict(model)
    return models.by_name(name)


def _choose(env, db, mid):
    return env.sync_mod.set_table_note_types(env.col, {db: mid})


def _model_name(env, rid):
    return env.note(rid).note_type()["name"]


def _touch(env, rid, stamp):
    env.notion.tables[DB1]["rows"][rid][
        1
    ] = f"2026-02-{stamp:02d}T00:00:00.000Z"


def _level(env, rid, value, stamp=3):
    _touch(env, rid, stamp)
    env.notion.props[rid] = {
        "Level": {
            "type": "rich_text",
            "rich_text": [{"plain_text": value}],
        }
    }


def test_eligibility(env):
    ok = _make_type(env, "Ok")
    _make_type(env, "Two", ("A", "Source"))
    _make_type(env, "NoSource", ("A", "B", "C"))
    _make_type(env, "SourceFirst", ("Source", "A", "B"))
    used = _make_type(env, "Used", ("A", "B", "Source"))
    note = env.col.new_note(used)
    note["Source"] = "mine"
    env.col.add_note(note, 1)
    env.sync()
    names = {n for _m, n in env.sync_mod.eligible_note_types(env.col)}
    assert names == {"Ok"}
    assert ok["id"] in dict(env.sync_mod.eligible_note_types(env.col))
    # once the add-on uses a type with notes in it, it stays eligible
    assert _choose(env, DB1, ok["id"])
    assert env.sync_mod.table_note_types(env.col) == {DB1: ok["id"]}


def test_new_rows_use_the_chosen_type_with_mapped_fields(env):
    mine = _make_type(env)
    assert _choose(env, DB1, mine["id"])
    env.notion.props[ROW] = {
        "level": {"type": "select", "select": {"name": "Hard <b>"}}
    }
    stats = env.sync()
    assert stats["added"] == 8
    note = env.note(ROW)
    assert note.note_type()["name"] == "Mine"
    assert note.fields[0] == "h0" and "hist 0" in note.fields[1]
    assert note.fields[2] == ROW
    assert note.fields[3] == "Hard &lt;b&gt;"
    assert _model_name(env, _rid(10)) == "Notion Sync Card"
    assert env.col.get_config(KEY)["tables"] == {DB1: mine["id"]}

    env.notion.downloads.clear()
    env.col.add_custom_undo_entry("Previous action")
    _assert_noop(env, env.sync(), 8)
    assert env.col.undo_status().undo == "Previous action"
    assert env.notion.downloads == []
    # a downloaded row with nothing new is a no-op too
    _touch(env, ROW, 5)
    stats = env.sync()
    assert stats["updated"] == 0 and stats["added"] == 0
    assert env.col.undo_status().undo == "Previous action"


def test_updates_write_the_same_mapping(env):
    mine = _make_type(env)
    _choose(env, DB1, mine["id"])
    env.sync()
    env.notion.tables[DB1]["rows"][ROW][2] = [_para("new page")]
    _level(env, ROW, "Easy")
    stats = env.sync()
    assert stats["updated"] == 1
    note = env.note(ROW)
    assert "new page" in note.fields[1] and note.fields[3] == "Easy"
    # only a column changed: the extra field still follows
    _level(env, ROW, "Medium", 4)
    assert env.sync()["updated"] == 1
    assert env.note(ROW).fields[3] == "Medium"


def test_existing_notes_are_untouched_by_a_new_choice(env):
    env.sync()
    ids = {rid: env.note(rid).id for rid in (ROW, OTHER)}
    mine = _make_type(env)
    _choose(env, DB1, mine["id"])
    env.col.add_custom_undo_entry("Previous action")
    env.notion.downloads.clear()
    _assert_noop(env, env.sync(), 8)
    assert env.col.undo_status().undo == "Previous action"
    # even after an edit the note keeps its type, and is updated in place
    env.notion.tables[DB1]["rows"][ROW][2] = [_para("changed")]
    _edit(env, ROW)
    stats = env.sync()
    assert stats["updated"] == 1 and stats["added"] == 0
    assert env.note(ROW).id == ids[ROW]
    assert _model_name(env, ROW) == "Notion Sync Card"
    assert "changed" in env.note(ROW).fields[1]
    assert env.count() == 8
    # a new row of that table uses the chosen type
    env.notion.tables[DB1]["rows"][_rid(5)] = [
        "new",
        "2020-01-01T00:00:00.000Z",
        [],
    ]
    assert env.sync()["added"] == 1
    assert _model_name(env, _rid(5)) == "Mine"
    assert env.sync()["added"] == 0


def test_switching_back_and_forth_makes_no_duplicates(env):
    a = _make_type(env, "A")
    b = _make_type(env, "B")
    _choose(env, DB1, a["id"])
    env.sync()
    first = env.note(ROW).id
    assert _choose(env, DB1, b["id"])
    env.notion.tables[DB1]["rows"][ROW][2] = [_para("v2")]
    _touch(env, ROW, 6)
    env.notion.tables[DB1]["rows"][_rid(6)] = [
        "x",
        "2020-01-01T00:00:00.000Z",
        [],
    ]
    stats = env.sync()
    assert stats["added"] == 1 and stats["updated"] == 1
    assert env.note(ROW).id == first and _model_name(env, ROW) == "A"
    assert _model_name(env, _rid(6)) == "B"
    assert _choose(env, DB1, 0)
    assert env.sync_mod.table_note_types(env.col) == {}
    env.notion.downloads.clear()
    _assert_noop(env, env.sync(), 9)
    for rid in (ROW, OTHER, _rid(6)):
        assert len(env.col.find_notes(f"Source:{rid}")) == 1
    # the old types keep being found: the rows are not removed
    assert set(env.sync_mod.synced_notes(env.col)) >= {ROW, _rid(6)}


def test_user_notes_of_the_chosen_type_are_ignored(env):
    mine = _make_type(env)
    _choose(env, DB1, mine["id"])
    env.sync()
    note = env.col.new_note(mine)
    note["Q"], note["Source"] = "hand", "my own source"
    env.col.add_note(note, 1)
    clash = env.col.new_note(mine)  # looks like a row id but is not hex
    clash["Q"], clash["Source"] = "hand2", HEX[:31] + "G"
    env.col.add_note(clash, 1)
    before = sorted(env.col.find_notes(""))
    env.col.add_custom_undo_entry("Previous action")
    env.notion.downloads.clear()
    _assert_noop(env, env.sync(), 8)
    assert sorted(env.col.find_notes("")) == before
    assert env.col.undo_status().undo == "Previous action"
    assert "my own source" not in env.sync_mod.synced_notes(env.col)


def test_cloze_rows_always_use_the_cloze_type(env):
    mine = _make_type(env)
    _choose(env, DB1, mine["id"])
    env.notion.tables[DB1]["rows"][ROW][0] = "A {{c1::cloze}}"
    env.sync()
    assert _model_name(env, ROW) == "Notion Sync Cloze"
    assert _model_name(env, OTHER) == "Mine"


def test_cloze_row_turning_basic_uses_the_chosen_type(env):
    mine = _make_type(env)
    env.notion.tables[DB1]["rows"][ROW][0] = "A {{c1::cloze}}"
    env.sync()
    _choose(env, DB1, mine["id"])
    env.notion.tables[DB1]["rows"][ROW][0] = "plain"
    _edit(env, ROW)
    env.sync()
    assert _model_name(env, ROW) == "Mine"
    assert len(env.col.find_notes(f"Source:{ROW}")) == 1


def test_a_removed_chosen_type_falls_back_with_a_warning(env):
    mine = _make_type(env)
    _choose(env, DB1, mine["id"])
    env.col.models.remove(mine["id"])
    assert env.sync_mod.table_note_types(env.col) == {}
    env.sync()
    assert _model_name(env, ROW) == "Notion Sync Card"


def test_set_table_note_types_validates_and_is_one_undo_step(env):
    mine = _make_type(env)
    env.col.add_custom_undo_entry("Previous action")
    assert not env.sync_mod.set_table_note_types(env.col, {DB1: 12345})
    assert env.col.undo_status().undo == "Previous action"
    assert env.sync_mod.set_table_note_types(
        env.col, {DB1: mine["id"], DB2: mine["id"]}
    )
    assert env.col.undo_status().undo != "Previous action"
    assert not env.sync_mod.set_table_note_types(env.col, {DB1: mine["id"]})
    env.col.undo()
    assert env.sync_mod.table_note_types(env.col) == {}
    env.sync()
    # the registry keeps the old role -> id format
    assert env.sync_mod.own_model_ids(env.col).keys() == {"basic"}


def test_dialog_lists_tables_and_reports_only_changes(qapp):  # noqa: F811
    from notion_sync_addon.settings_ui import SettingsDialog

    saved: list = []
    dialog = SettingsDialog(
        {"notion_token": "t", "notion_pages": [P1]},
        canonical_page_id,
        lambda pid, token: "Hist",
        lambda c: None,
        lambda: None,
        tables=[(P1, [(DB1, "Defs"), (DB2, "Other")])],
        note_types=[(7, "Mine"), (8, "Yours")],
        table_types={DB2: 8},
        on_note_types=saved.append,
    )
    assert set(dialog._type_combos) == {DB1, DB2}
    assert dialog._type_combos[DB2].currentData() == 8
    assert dialog._type_combos[DB1].count() == 3
    assert dialog.chosen_note_types() == {}
    dialog._type_combos[DB1].setCurrentIndex(
        dialog._type_combos[DB1].findData(7)
    )
    dialog._type_combos[DB2].setCurrentIndex(0)
    assert dialog.save()
    assert saved == [{DB1: 7, DB2: 0}]
    assert dialog.chosen_note_types() == {}
    assert dialog.save() and len(saved) == 1


def test_dialog_without_tables_still_works(qapp):  # noqa: F811
    from notion_sync_addon.settings_ui import SettingsDialog

    dialog = SettingsDialog(
        {"notion_token": "t", "notion_pages": [P1]},
        canonical_page_id,
        lambda pid, token: "Hist",
        lambda c: None,
        lambda: None,
    )
    assert dialog._type_combos == {} and dialog.chosen_note_types() == {}
    assert dialog.save()
