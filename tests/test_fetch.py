# -*- coding: utf-8 -*-
"""Reading Notion: which rows are downloaded, and how often."""

import os
import tempfile
import time
from typing import Dict, cast

import pytest

from notion_sync_addon.htmlify import MediaCollector
from notion_sync_addon.notion_api import NotionClient
from notion_sync_addon.sync import RowState, SyncResult, _fetch_table
from tests.helpers import (
    DB1,
    DB2,
    DB3,
    EDITED,
    EDITED_TS,
    OLD,
    P2,
    _image,
    _mention,
    _para,
    _rid,
    _rt,
)


def test_empty_cards_are_enabled_by_default():
    class Client:
        def get_database(self, database_id):
            return {
                "title": [{"plain_text": "Table"}],
                "data_sources": [{"id": "ds"}],
            }

        def query_data_source(self, data_source_id):
            return [
                {
                    "id": "row-1",
                    "properties": {"Name": {"type": "title", "title": []}},
                }
            ]

    class Tree:
        def children(self, block_id):
            return []

    result = SyncResult()
    result.config = {}
    client = cast(NotionClient, Client())
    table = _fetch_table(
        client, Tree(), {"id": "db-1"}, MediaCollector(), "Page", result
    )
    assert table is not None
    assert len(table.rows) == 1
    assert table.rows[0].front == ""

    result.config = {"include_empty_cards": False}
    client = cast(NotionClient, Client())
    table = _fetch_table(
        client, Tree(), {"id": "db-1"}, MediaCollector(), "Page", result
    )
    assert table is not None
    assert table.rows == []


class _RowsClient:
    def __init__(self, rows):
        self.rows = rows

    def get_database(self, database_id):
        return {
            "title": [{"plain_text": "Defs"}],
            "data_sources": [{"id": "ds"}],
        }

    def query_data_source(self, data_source_id):
        return self.rows


def _row(row_id, title, edited):
    return {
        "id": row_id,
        "last_edited_time": edited,
        "url": "",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": title}]}
        },
    }


def test_unchanged_rows_skip_content_download(frozen_now):
    fetched = []

    class Tree:
        def children(self, block_id):
            fetched.append(block_id)
            return [
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "plain_text": "body",
                                "annotations": {},
                                "type": "text",
                                "text": {"content": "body"},
                            }
                        ]
                    },
                }
            ]

    later = "2026-11-04T11:45:00.000Z"
    client = _RowsClient(
        [_row("aaa", "Ideologie", EDITED), _row("bbb", "Racisme", later)]
    )
    result = SyncResult()
    result.known = {
        "aaa": RowState(EDITED, EDITED_TS + 600, "fp"),
        "bbb": RowState(EDITED, EDITED_TS + 600, "fp"),
    }
    table = _fetch_table(
        cast(NotionClient, client),
        Tree(),
        {"id": "db"},
        MediaCollector(),
        "Page",
        result,
        "page",
    )

    assert fetched == ["bbb"]  # only the edited row is downloaded
    assert table is not None
    by_id = {r.source_id: r for r in table.rows}
    assert by_id["aaa"].unchanged and by_id["aaa"].back_html == ""
    assert not by_id["bbb"].unchanged and "body" in by_id["bbb"].back_html
    assert result.fetched["aaa"] == RowState(
        EDITED, EDITED_TS + 600, "fp", "page", "db"
    )
    assert result.fetched["bbb"].edited == later
    assert (result.fetched["bbb"].page_id, result.fetched["bbb"].db_id) == (
        "page",
        "db",
    )
    assert not result.incomplete


def test_unchanged_page_reuses_table_list_and_data_source(
    frozen_now, monkeypatch
):
    from notion_sync_addon.sync import fetch_pages

    calls = []

    class Client:
        def get_page(self, page_id):
            calls.append("page")
            return {
                "last_edited_time": EDITED,
                "properties": {
                    "t": {"type": "title", "title": [{"plain_text": "Hist"}]}
                },
            }

        def get_database(self, database_id):
            calls.append("database")
            return {
                "title": [{"plain_text": "Defs"}],
                "data_sources": [{"id": "ds-1"}],
            }

        def query_data_source(self, data_source_id):
            calls.append("query:" + data_source_id)
            return []

    class Tree:
        def children(self, block_id):
            calls.append("blocks")
            return [
                {
                    "id": "db-1",
                    "type": "child_database",
                    "child_database": {"title": "Defs"},
                }
            ]

    import notion_sync_addon.fetch as fetch_mod

    page_id = "1f2e3d4c5b6a79881726354433221100"
    monkeypatch.setattr(fetch_mod, "NotionClient", lambda token: Client())
    monkeypatch.setattr(fetch_mod, "BlockTree", lambda client: Tree())
    cfg = {"notion_token": "t", "notion_pages": [page_id]}
    first = fetch_pages(cfg)
    assert calls == ["page", "blocks", "database", "query:ds-1"]
    structure: Dict[str, dict] = {
        "pages": dict(first.pages_out),
        "dbs": dict(first.dbs_out),
    }
    # pretend the first fetch happened well after the page's last edit
    structure["pages"][page_id][1] = EDITED_TS + 600
    calls.clear()
    second = fetch_pages(cfg, {}, None, structure)
    # no block walk, no database lookup: just the page and the row query
    assert calls == ["page", "query:ds-1"]
    assert [t.title for p in second.pages for t in p.tables] == ["Defs"]
    first.cleanup()
    second.cleanup()


def test_fetch_cleans_temp_dir_on_unexpected_error(monkeypatch):
    import notion_sync_addon.fetch as fetch_mod
    import notion_sync_addon.sync as sync_mod

    made = []
    real_mkdtemp = tempfile.mkdtemp

    def mkdtemp(**kw):
        made.append(real_mkdtemp(**kw))
        return made[-1]

    class Boom:
        def __init__(self, token):
            pass

        def get_page(self, page_id):
            raise ValueError("not a NotionError")

    monkeypatch.setattr(tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr(fetch_mod, "NotionClient", Boom)
    with pytest.raises(ValueError):
        sync_mod.fetch_pages({"notion_token": "t", "notion_pages": ["a" * 32]})
    assert made and not os.path.exists(made[0])


def test_full_resync_bypasses_every_cache(env):
    env.sync()
    env.notion.downloads.clear()
    stats = env.sync(full=True)
    assert stats["unchanged"] == 0 and len(env.notion.downloads) == 8


def test_replaced_data_source_is_looked_up_again(env):
    """N2: a remembered data source id that Notion rejects is renewed."""
    env.sync()
    env.notion.ds[DB1] = "ds-new-" + DB1  # the old one now answers 404
    env.notion.tables[DB1]["rows"][_rid(5)] = ["h5", OLD, [_para("new row")]]
    env.notion.calls.clear()
    stats = env.sync()
    assert not env.result.incomplete and stats["added"] == 1
    assert ("db", DB1) in env.notion.calls
    assert env.sync_mod.load_cache(env.path).dbs[DB1] == "ds-new-" + DB1
    env.notion.calls.clear()
    env.sync()
    assert ("db", DB1) not in env.notion.calls  # cached again


def test_deleted_database_fails_its_table_only(env):
    """N2: when the lookup fails too, the table is failed (scoped) and the
    stale id is forgotten, so later syncs don't query it first."""
    env.sync()
    del env.notion.tables[DB3]
    env.notion.pages[P2]["tables"] = [DB2, DB3]
    del env.notion.tables[DB1]["rows"][_rid(0)]
    stats = env.sync()
    assert env.result.incomplete and DB3 in env.result.failed_dbs
    assert stats["removed"] == 1 and env.count() == 7  # DB3's cards are kept
    assert DB3 not in env.sync_mod.load_cache(env.path).dbs


def test_transient_query_error_does_not_look_up_the_database(env):
    env.sync()
    env.notion.fail = {("query", DB2)}
    env.notion.calls.clear()
    env.sync()
    assert ("db", DB2) not in env.notion.calls
    assert DB2 in env.sync_mod.load_cache(env.path).dbs


def test_synced_block_copies_are_rechecked_hourly(env, monkeypatch):
    synced = {
        "id": "sb",
        "type": "synced_block",
        "has_children": True,
        "synced_block": {"synced_from": {"block_id": "elsewhere"}},
    }
    env.notion.tables[DB1]["rows"][_rid(0)][2] = [_para("x"), synced]
    env.sync()
    env.notion.downloads.clear()
    env.sync()
    assert (
        env.notion.downloads == []
    )  # a no-op right after: nothing downloaded
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3601)
    env.sync()
    assert env.notion.downloads == [_rid(0)]


def test_mentions_anywhere_make_a_row_volatile():
    """N3: mentions in table cells, captions and the row title are found."""
    from notion_sync_addon.sync import _has_live_mention, _is_volatile

    cell_row = {
        "type": "table_row",
        "table_row": {"cells": [_rt("a"), [_mention()]]},
    }
    assert _is_volatile(cell_row)
    caption = _image("i" * 32, "a.png")
    caption["image"]["caption"] = [_mention(kind="database")]
    assert _is_volatile(caption)
    assert _is_volatile(
        {"type": "callout", "callout": {"rich_text": [_mention(kind="user")]}}
    )
    assert not _is_volatile(
        {
            "type": "paragraph",
            "paragraph": {"rich_text": [_mention(kind="date")]},
        }
    )
    assert not _is_volatile(
        {"type": "table_row", "table_row": {"cells": [_rt("a")]}}
    )
    assert _has_live_mention([_mention()]) and not _has_live_mention(_rt("x"))


def test_rows_with_mentions_are_rechecked_hourly_not_every_sync(
    env, monkeypatch
):
    """N3: a no-op sync right after a (full) sync downloads no row content."""
    table = {
        "id": "tb",
        "type": "table",
        "has_children": True,
        "table": {"has_column_header": False},
    }
    env.notion.tables[DB1]["rows"][_rid(0)][2] = [table]  # mention in a cell
    # mention in the row title
    env.notion.tables[DB1]["rows"][_rid(1)].append(
        [{"plain_text": "see "}, _mention()]
    )
    real_children = env.notion.children

    def children(bid):
        if bid == "tb":
            return [
                {
                    "id": "tr",
                    "type": "table_row",
                    "table_row": {"cells": [[_mention()]]},
                }
            ]
        return real_children(bid)

    monkeypatch.setattr(env.notion, "children", children)
    env.sync(full=True)
    state = env.sync_mod.load_cache(env.path).rows
    assert state[_rid(0)].volatile and state[_rid(1)].volatile
    assert not state[_rid(2)].volatile
    env.notion.downloads.clear()
    assert env.sync()["unchanged"] == 8 and env.notion.downloads == []
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3601)
    env.sync()
    assert sorted(env.notion.downloads) == [_rid(0), _rid(1)]


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


def _image_named(name):
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
        rows[_rid(i)][2] = [_image_named(f"a{i}")]
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
