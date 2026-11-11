# -*- coding: utf-8 -*-
"""Renaming a Notion page or table moves its existing cards."""

from tests.helpers import DB1, DB2, P1, P2, _rid

NEW = "2026-01-02T00:00:00.000Z"


def _deck_of(env, row_id):
    note = env.note(row_id)
    card = env.col.get_card(env.col.card_ids_of_note(note.id)[0])
    return env.col.decks.name(card.did)


def _rename_table(env, db, page, title):
    env.notion.tables[db]["title"] = title
    env.notion.pages[page]["edited"] = NEW


def test_table_rename_moves_cards_in_one_undo_step(env):
    env.sync()
    _rename_table(env, DB1, P1, "Terms")
    env.col.add_custom_undo_entry("Previous action")
    stats = env.sync()
    assert stats["updated"] == 3 and stats["added"] == 0
    for i in range(3):
        assert _deck_of(env, _rid(i)) == "Hist::Terms"
    assert env.col.decks.id_for_name("Hist::Defs") is not None
    assert env.sync_mod.load_cache(env.path).decks[DB1] == "Hist::Terms"
    assert env.col.undo_status().undo == "Notion Sync"
    env.col.undo()
    assert _deck_of(env, _rid(0)) == "Hist::Defs"
    assert env.col.undo_status().undo == "Previous action"


def test_page_rename_moves_all_tables(env):
    env.sync()
    env.notion.pages[P2]["title"] = "Logic"
    env.notion.pages[P2]["edited"] = NEW
    assert env.sync()["updated"] == 5
    assert _deck_of(env, _rid(10)) == "Logic::Notions"
    assert _deck_of(env, _rid(20)) == "Logic::Auteurs"


def test_card_moved_by_user_stays(env):
    env.sync()
    other = env.col.decks.id("Mine", create=True)
    env.col.set_deck(env.col.card_ids_of_note(env.note(_rid(1)).id), other)
    _rename_table(env, DB1, P1, "Terms")
    assert env.sync()["updated"] == 2
    assert _deck_of(env, _rid(1)) == "Mine"
    assert _deck_of(env, _rid(0)) == "Hist::Terms"


def test_hand_made_note_in_old_deck_stays(env):
    from anki.notes import Note

    env.sync()
    basic = env.col.models.by_name("Basic")
    note = Note(env.col, basic)
    note.fields[0] = "mine"
    env.col.add_note(note, env.col.decks.id("Hist::Defs"))
    _rename_table(env, DB1, P1, "Terms")
    env.sync()
    card = env.col.get_card(env.col.card_ids_of_note(note.id)[0])
    assert env.col.decks.name(card.did) == "Hist::Defs"


def test_failed_table_moves_nothing_and_keeps_record(env):
    env.sync()
    _rename_table(env, DB1, P1, "Terms")
    env.notion.fail = {("query", DB1)}
    env.sync()
    assert _deck_of(env, _rid(0)) == "Hist::Defs"
    assert env.sync_mod.load_cache(env.path).decks[DB1] == "Hist::Defs"
    env.notion.fail = set()
    env.sync()
    assert _deck_of(env, _rid(0)) == "Hist::Terms"


def test_old_cache_without_decks_only_records(env):
    env.sync()
    cache = env.sync_mod.load_cache(env.path)
    cache.decks = {}
    env.sync_mod.save_cache(env.path, cache)
    assert "decks" not in open(env.path, encoding="utf-8").read()
    _rename_table(env, DB1, P1, "Terms")
    assert env.sync()["updated"] == 0
    assert _deck_of(env, _rid(0)) == "Hist::Defs"
    assert env.sync_mod.load_cache(env.path).decks[DB1] == "Hist::Terms"


def test_decks_are_parsed_leniently(tmp_path):
    from notion_sync_addon.sync import load_cache

    path = str(tmp_path / "c.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"v": 1, "decks": {"a": "A::B", "b": 3, "c": ""}}')
    assert load_cache(path).decks == {"a": "A::B"}


def _move_row(env, row_id, src, dst):
    row = env.notion.tables[src]["rows"].pop(row_id)
    row[1] = "2026-01-02T00:00:00.000Z"
    env.notion.tables[dst]["rows"][row_id] = row


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
