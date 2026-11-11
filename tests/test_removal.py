# -*- coding: utf-8 -*-
"""When synced notes are removed, and when they must be kept."""

import os
from typing import Any, List, cast

from notion_sync_addon.notion_api import NotionError
from tests.helpers import DB1, DB2, DB3, P1, P2, _rid


def test_failed_table_lookup_never_drops_its_cards(env):
    """Review finding 1: P1 fails, then P2's table lookup fails."""
    env.sync()
    env.notion.pages[P2]["edited"] = "2026-01-02T00:00:00.000Z"
    env.notion.fail = {("page", P1), ("db", DB2)}
    # forget DB2's data source id so GET /databases is needed
    cache = env.sync_mod.load_cache(env.path)
    cache.dbs.pop(DB2)
    env.sync_mod.save_cache(env.path, cache)
    stats = env.sync()
    assert stats["removed"] == 0 and env.result.incomplete
    assert env.sync_mod.load_cache(env.path).pages[P2][2] == [
        [DB2, "Notions"],
        [DB3, "Auteurs"],
    ]
    env.notion.fail = set()
    stats = env.sync()
    assert stats["removed"] == 0 and env.count() == 8


def test_removal_is_scoped_to_the_failed_table(env):
    env.sync()
    del env.notion.tables[DB1]["rows"][_rid(0)]  # deleted in a healthy table
    del env.notion.tables[DB2]["rows"][
        _rid(10)
    ]  # deleted in the failing table
    env.notion.fail = {("query", DB2)}
    stats = env.sync()
    assert env.result.incomplete and stats["removed"] == 1
    assert env.note(_rid(0)) is None and env.note(_rid(10)) is not None
    env.notion.fail = set()
    assert env.sync()["removed"] == 1 and env.note(_rid(10)) is None


def test_failed_page_protects_all_its_cards(env):
    env.sync()
    env.notion.fail = {("page", P2)}
    stats = env.sync()
    assert stats["removed"] == 0 and env.count() == 8


def test_unreadable_row_is_kept(env):
    env.sync()
    env.notion.tables[DB1]["rows"][_rid(1)][1] = "2026-01-03T00:00:00.000Z"
    env.notion.fail = {("row", _rid(1))}
    assert env.sync()["removed"] == 0 and env.note(_rid(1)) is not None


def test_note_without_cache_entry_is_kept_when_anything_failed(env):
    env.sync()
    os.remove(env.path)  # no origins known
    env.notion.fail = {("query", DB3)}
    del env.notion.tables[DB1]["rows"][_rid(0)]
    assert env.sync()["removed"] == 0
    env.notion.fail = set()
    assert env.sync()["removed"] == 1


def test_invalid_page_entry_does_not_block_removal(env):
    """N4a: a mistyped entry is a config warning; deletions elsewhere go on."""
    env.sync()
    env.cfg["notion_pages"] = [P1, P2, "not a page"]
    del env.notion.tables[DB1]["rows"][_rid(0)]
    stats = env.sync()
    assert stats["removed"] == 1 and not env.result.incomplete
    assert any("'not a page'" in w for w in env.result.warnings)
    assert any(
        "'not a page'" in p for p in env.sync_mod.load_cache(env.path).problems
    )


def test_page_mangled_into_an_invalid_entry_keeps_its_cards(env):
    """N4a: P2's link edited into garbage: its cards may be that page's, keep
    them; a valid page removed from the config still loses its cards."""
    env.sync()
    env.cfg["notion_pages"] = [P1, "https://www.notion.so/oops-" + P2[:20]]
    stats = env.sync()
    assert stats["removed"] == 0 and env.count() == 8
    env.cfg["notion_pages"] = [P1]
    assert env.sync()["removed"] == 5 and env.count() == 3


def test_unlisted_notes_of_unknown_origin_are_looked_up(env):
    """N4b: cache lost and a table failing: a row Notion has in the trash is
    removed, a row that still exists (in the failing table) is kept."""
    env.sync()
    os.remove(env.path)
    env.notion.fail = {("query", DB3)}
    env.notion.trashed[_rid(0)] = DB1
    del env.notion.tables[DB1]["rows"][_rid(0)]
    env.notion.calls.clear()
    stats = env.sync()
    assert env.result.incomplete and stats["removed"] == 1
    assert env.note(_rid(0)) is None and env.note(_rid(20)) is not None
    looked_up = [
        c for c in env.notion.calls if c[0] == "page" and c[1] not in (P1, P2)
    ]
    assert sorted(c[1] for c in looked_up) == [_rid(0), _rid(20), _rid(21)]
    # origins learned for the kept rows: not asked again
    rows = env.sync_mod.load_cache(env.path).rows
    assert (rows[_rid(20)].page_id, rows[_rid(20)].db_id) == (P2, DB3)
    env.notion.calls.clear()
    env.sync()
    assert all(c[1] in (P1, P2) for c in env.notion.calls if c[0] == "page")


def test_unknown_origin_lookup_error_keeps_the_note(env):
    env.sync()
    os.remove(env.path)
    env.notion.fail = {("query", DB3)}
    del env.notion.tables[DB1]["rows"][_rid(0)]  # 404 (maybe just not shared)
    assert env.sync()["removed"] == 0 and env.note(_rid(0)) is not None


def _unlisted_result(n):
    from notion_sync_addon.sync import SyncResult

    result = SyncResult()
    result.sources = {f"{i:032x}" for i in range(n)}
    return result


def test_unlisted_checks_rotate_through_rows_that_keep_failing(monkeypatch):
    import notion_sync_addon.sync as sync_mod

    asked: List[str] = []

    class Client:
        def get_page(self, pid):
            asked.append(pid)
            raise NotionError("404", 404)

    total = sync_mod.MAX_ORIGIN_CHECKS + 5
    result = _unlisted_result(total)
    sync_mod._check_unlisted(cast(Any, Client()), result)
    assert len(asked) == sync_mod.MAX_ORIGIN_CHECKS
    assert any("5 card(s)" in w for w in result.warnings)
    assert not result.confirmed_gone and not result.protected

    # next sync: the cache remembers who was asked; the rest comes first
    nxt = _unlisted_result(total)
    nxt.previous.checked = dict(result.checked)
    first = set(asked)
    asked.clear()
    sync_mod._check_unlisted(cast(Any, Client()), nxt)
    assert set(asked) == nxt.sources - first and len(asked) == 5

    # after a day the first rows are due again
    later = _unlisted_result(total)
    day2 = 2 * 24 * 3600
    later.previous.checked = {
        k: t - day2 for k, t in {**result.checked, **nxt.checked}.items()
    }
    asked.clear()
    sync_mod._check_unlisted(cast(Any, Client()), later)
    assert len(asked) == sync_mod.MAX_ORIGIN_CHECKS


def test_hand_made_note_with_any_source_is_never_touched(env):
    """N8: only a Notion row id in Source marks a synced note."""
    from anki.notes import Note

    env.sync()
    model = env.col.models.by_name("Notion Sync Card")
    did = env.note(_rid(0)).cards()[0].did
    mine = []
    for source in ("Book p. 12", "F" * 32, "abc", _rid(0)[:31]):
        note = Note(env.col, model)
        note["Front"], note["Back"], note["Source"] = "mine", "by hand", source
        env.col.add_note(note, did)
        mine.append(note.id)
    stats = env.sync()
    assert stats["removed"] == 0 and stats["unchanged"] == 8
    assert all(env.col.get_note(nid)["Back"] == "by hand" for nid in mine)
    assert set(env.sync_mod.synced_notes(env.col)) == set(
        env.sync_mod.load_cache(env.path).rows
    )


def test_duplicates_keep_the_oldest_note(env):
    from anki.notes import Note

    env.sync()
    original = env.note(_rid(0))
    did = original.cards()[0].did
    model = env.col.models.by_name("Notion Sync Card")

    def add(front, back, source):
        note = Note(env.col, model)
        note["Front"], note["Back"], note["Source"] = front, back, source
        env.col.add_note(note, did)
        return note

    add("h0", "copy", _rid(0))
    manual = add("mine", "added by hand", "")
    assert env.sync()["removed"] == 1
    assert env.col.find_notes(f"Source:{_rid(0)}") == [original.id]
    assert env.col.get_note(manual.id)["Back"] == "added by hand"
    # duplicates go even when part of Notion can't be read
    add("h0", "copy", _rid(0))
    env.notion.fail = {("page", P2)}
    assert env.sync()["removed"] == 1
    assert env.col.find_notes(f"Source:{_rid(0)}") == [original.id]


def test_same_row_twice_in_one_sync_makes_one_note(env):
    """N7: a page listed twice, or a row listed by two tables."""
    env.cfg["notion_pages"] = [P1, P2, P1, "https://www.notion.so/Hist-" + P1]
    env.notion.tables[DB3]["rows"][_rid(0)] = list(
        env.notion.tables[DB1]["rows"][_rid(0)]
    )
    stats = env.sync()
    assert stats["added"] == 8 and env.count() == 8
    assert len(env.col.find_notes(f"Source:{_rid(0)}")) == 1
    assert env.result.page_ids == [P1, P2]


def test_deleted_note_is_recreated(env):
    env.sync()
    env.col.remove_notes([env.note(_rid(2)).id])
    assert env.sync()["added"] == 1 and env.note(_rid(2)) is not None
