# -*- coding: utf-8 -*-
"""Writing the notes: updates, undo, titles and the note type."""

from notion_sync_addon.sync import MODEL_CSS, _get_or_create_model
from tests.helpers import DB1, OLD, _bold, _image, _para, _rid, _rt


def test_first_sync_then_noop_is_cached_and_not_undoable(env):
    first = env.sync()
    assert first["added"] == 8 and env.count() == 8
    assert env.col.undo_status().undo == "Notion Sync"
    env.notion.downloads.clear()
    env.col.add_custom_undo_entry("Previous action")
    second = env.sync()
    assert second == {
        "added": 0,
        "updated": 0,
        "removed": 0,
        "unchanged": 8,
        "media": 0,
    }
    assert env.notion.downloads == []
    assert env.result.changes is None
    assert env.col.undo_status().undo == "Previous action"  # no new undo entry


def test_large_sync_is_still_one_undo_step(env):
    """Anki keeps ~30 undo steps; 100 note changes still make one."""
    env.notion.tables[DB1]["rows"].update(
        {_rid(100 + i): [f"x{i}", OLD, [_para(f"x {i}")]] for i in range(100)}
    )
    env.col.add_custom_undo_entry("Previous action")
    assert env.sync()["added"] == 108
    assert env.col.undo_status().undo == "Notion Sync"
    env.col.undo()
    assert env.count() == 0
    assert env.col.undo_status().undo == "Previous action"


def test_edit_in_anki_is_overwritten_by_notion(env):
    env.sync()
    note = env.note(_rid(0))
    note["Back"] = "EDITED IN ANKI"
    env.col.update_note(note)
    env.notion.downloads.clear()
    stats = env.sync()
    assert stats["updated"] == 1 and env.notion.downloads == [_rid(0)]
    assert "hist 0" in env.note(_rid(0))["Back"]


def test_undo_of_a_sync_heals_on_next_sync(env):
    env.sync()
    env.notion.tables[DB1]["rows"][_rid(0)] = [
        "h0",
        "2026-01-05T00:00:00.000Z",
        [_para("v2")],
    ]
    assert env.sync()["updated"] == 1
    env.col.undo()
    assert "hist 0" in env.note(_rid(0))["Back"]
    assert env.sync()["updated"] == 1
    assert "v2" in env.note(_rid(0))["Back"]


def test_edit_while_fetching_is_caught_at_apply(env):
    env.sync()

    def edit():
        note = env.note(_rid(1))
        note["Back"] = "changed during fetch"
        env.col.update_note(note)

    env.notion.downloads.clear()
    stats = env.sync(between=edit)
    assert stats["unchanged"] == 7 and env.notion.downloads == []
    stats = env.sync()
    assert stats["updated"] == 1 and "hist 1" in env.note(_rid(1))["Back"]


def test_full_resync_of_identical_content_changes_nothing(env):
    """N1: a re-download must produce exactly the stored HTML (media names
    included), on every platform: 0 updates, no undo step."""
    env.notion.tables[DB1]["rows"][_rid(0)][2] = [
        _para("Café <b> & x"),
        _image("1a2b" + "0" * 28, "Screen%20Shot.png"),
    ]
    env.notion.tables[DB1]["rows"][_rid(1)][2] = [
        _image("3c4d" + "0" * 28, "photo")
    ]
    env.sync()
    back = env.note(_rid(0))["Back"]
    assert 'src="ns-1a2b' in back and "screen_20shot.png" in back
    env.col.add_custom_undo_entry("Previous action")
    stats = env.sync(full=True)
    assert (
        stats["updated"] == 0 and stats["added"] == 0 and stats["removed"] == 0
    )
    assert env.col.undo_status().undo == "Previous action"
    assert env.note(_rid(0))["Back"] == back


def test_row_title_is_escaped_in_the_front_field(env):
    env.notion.tables[DB1]["rows"][_rid(0)][0] = "What is <div> & a < b?"
    env.sync()
    front = env.note(_rid(0)).fields[0]
    assert front == "What is &lt;div&gt; &amp; a &lt; b?"


def _equation(expression):
    return [
        {
            "type": "equation",
            "plain_text": expression,
            "equation": {"expression": expression},
        }
    ]


def _titled(env, row_id, rich):
    env.notion.tables[DB1]["rows"][row_id] = ["", OLD, [_para("b")], rich]


def test_a_row_title_keeps_its_formatting(env):
    """I5: bold, inline code and equations reach the Front field."""
    code = _rt("x")
    code[0]["annotations"]["code"] = True
    _titled(env, _rid(0), _bold("Why") + _rt(" is a < b and ") + code)
    _titled(env, _rid(1), _rt("Solve ") + _equation("a<b"))
    env.sync()
    assert env.note(_rid(0))["Front"] == (
        "<b>Why</b> is a &lt; b and <code>x</code>"
    )
    assert env.note(_rid(1))["Front"] == r"Solve \(a&lt;b\)"


def test_a_formatted_cloze_title_stays_a_cloze_and_messages_are_plain(env):
    """I5: cloze detection and warnings read the plain title."""
    _titled(env, _rid(0), _rt("{{c1::") + _bold("Paris") + _rt("}} is"))
    # formatting that cuts the marker: the plain title is written instead
    _titled(env, _rid(1), _rt("{{c") + _bold("1") + _rt("::Rome}}"))
    _titled(env, _rid(2), _bold("Plain"))
    env.notion.props[_rid(2)] = {
        "Card type": {"type": "select", "select": {"name": "Cloze"}}
    }
    env.sync()
    paris = env.note(_rid(0))
    assert paris.note_type()["name"] == "Notion Sync Cloze"
    assert paris["Text"] == "{{c1::<b>Paris</b>}} is"
    assert env.note(_rid(1))["Text"] == "{{c1::Rome}}"
    assert any("Row 'Plain'" in w for w in env.result.warnings)


def test_model_creation_leaves_backend_managed_id():
    class Models:
        def by_name(self, name):
            return None

        def new(self, name):
            return {"name": name, "id": 0, "flds": [], "tmpls": []}

        def new_field(self, name):
            return {"name": name}

        def add_field(self, model, field):
            model["flds"].append(field)

        def add_template(self, model, template):
            model["tmpls"].append(template)

        def add_dict(self, model):
            assert model["id"] == 0

    class Collection:
        models = Models()

    model = _get_or_create_model(Collection())
    assert model["id"] == 0
    assert model["css"] == MODEL_CSS


def test_css_edited_in_anki_is_kept_and_noop_sync_adds_no_undo(env):
    env.sync()
    model = env.col.models.by_name("Notion Sync Card")
    model["css"] = ".card {}"
    env.col.models.save(model)
    env.col.add_custom_undo_entry("Previous action")
    env.sync()
    assert env.col.undo_status().undo == "Previous action"
    assert env.col.models.by_name("Notion Sync Card")["css"] == ".card {}"


def test_new_note_type_uses_the_chosen_style(env):
    from notion_sync_addon.styles import PRESETS

    env.cfg["card_style"] = "minimal"
    env.sync()
    model = env.col.models.by_name("Notion Sync Card")
    assert model["css"] == PRESETS["minimal"]
