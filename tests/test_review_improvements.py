# -*- coding: utf-8 -*-
"""Improvement points of docs/review.md (I1, I3, I5, I2)."""

import os

from tests.test_htmlify_and_sync import DB1, DB2, OLD, _para, _rid, _rt


def test_rows_of_every_data_source_are_read(env):
    """I1: a database with two data sources syncs the rows of both."""
    env.notion.second_source[DB1] = {_rid(2)}
    assert env.sync()["added"] == 8
    assert env.note(_rid(2)) is not None
    assert ("query", "ds2-" + DB1) in env.notion.calls
    # the cache keeps both ids: the next sync needs no database lookup
    env.notion.calls.clear()
    assert env.sync()["unchanged"] == 8
    assert ("db", DB1) not in env.notion.calls
    assert ("query", "ds2-" + DB1) in env.notion.calls


def _move_row(env, row_id, src, dst):
    row = env.notion.tables[src]["rows"].pop(row_id)
    row[1] = "2026-01-02T00:00:00.000Z"
    env.notion.tables[dst]["rows"][row_id] = row


def _deck_of(env, row_id):
    note = env.note(row_id)
    card = env.col.get_card(env.col.card_ids_of_note(note.id)[0])
    return env.col.decks.name(card.did)


def test_a_row_moved_to_another_table_moves_its_cards(env):
    """I3: like a renamed table, only cards still in the old deck move."""
    env.sync()
    mine = env.col.decks.id("Mine", create=True)
    env.col.set_deck(env.col.card_ids_of_note(env.note(_rid(1)).id), mine)
    _move_row(env, _rid(0), DB1, DB2)
    _move_row(env, _rid(1), DB1, DB2)
    env.sync()
    assert _deck_of(env, _rid(0)) == "Philo::Notions"
    assert _deck_of(env, _rid(1)) == "Mine"  # moved by the user: stays
    assert _deck_of(env, _rid(2)) == "Hist::Defs"
    assert env.sync()["updated"] == 0


def _bold(text):
    out = _rt(text)
    out[0]["annotations"]["bold"] = True
    return out


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


def test_a_page_card_question_keeps_its_formatting(env):
    """I5: the same rule for cards written in a normal page."""
    from tests.test_htmlify_and_sync import P1

    question = {
        "id": "b001".ljust(32, "0"),
        "type": "heading_3",
        "has_children": False,
        "heading_3": {"rich_text": _bold("Why") + _rt("?")},
    }
    answer = _para("Because.") | {"id": "b002".ljust(32, "0")}
    env.notion.page_blocks[P1] = [question, answer]
    env.sync()
    assert env.note("b001".ljust(32, "0"))["Front"] == "<b>Why</b>?"


def _image(name):
    return {
        "id": name.ljust(32, "0"),
        "type": "image",
        "has_children": False,
        "image": {
            "type": "file",
            "file": {"url": f"https://s3/{name}.png"},
            "caption": [],
        },
    }


def test_rows_download_side_by_side_and_keep_their_order(env, monkeypatch):
    """I2: a table's rows are read three at a time; the cards, warnings
    and media still come in the table's order."""
    import threading

    rows = env.notion.tables[DB1]["rows"]
    for i in range(3):
        rows[_rid(i)][2] = [_image(f"a{i}")]
    together = threading.Barrier(3, timeout=10)
    real = env.notion.children

    def children(block_id):
        if block_id in rows:
            together.wait()  # fails unless the three rows are read at once
        return real(block_id)

    monkeypatch.setattr(env.notion, "children", children)
    assert env.sync()["added"] == 8
    nids = [env.note(_rid(i)).id for i in range(3)]
    assert nids == sorted(nids)  # added in the table's order
    names = [os.path.basename(path) for path, _name in env.result.media_files]
    assert [n.split("-")[1][:2] for n in names] == ["a0", "a1", "a2"]
