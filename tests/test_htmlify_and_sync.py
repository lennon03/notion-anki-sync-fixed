# -*- coding: utf-8 -*-
import os

import pytest

from notion_sync_addon.htmlify import MediaCollector, render_blocks
from notion_sync_addon.notion_api import NotionClient, NotionError
from notion_sync_addon.sync import (
    MODEL_CSS,
    RowState,
    SyncResult,
    _get_or_create_model,
    _fetch_table,
    canonical_page_id,
    deck_safe,
    is_unchanged,
    resolve_token,
)


class FakeTree:
    """children(block_id) -> pre-registered list of blocks."""

    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    def children(self, block_id):
        return self.mapping.get(block_id, [])


def _children(b):
    return b.has_children


def test_inline_rich_text_annotations():
    payload = [
        {"type": "text", "plain_text": "Hello", "text": {"content": "Hello", "link": None},
         "annotations": {"bold": True, "italic": True, "strikethrough": False,
                         "underline": True, "code": False, "color": "default"}},
        {"type": "equation", "plain_text": "1+1", "equation": {"expression": "F=ma"}},
        {"type": "text", "plain_text": "x", "text": {"content": "x", "link": {"url": "https://example.com"}},
         "annotations": {"bold": False, "italic": False, "strikethrough": False,
                         "underline": False, "code": False, "color": "default"}},
    ]
    out = render_blocks([{
        "type": "paragraph", "has_children": False,
        "paragraph": {"rich_text": payload}, "id": "b1",
    }], MediaCollector(), FakeTree())
    assert "<u><em><b>Hello</b></em></u>" in out
    assert "\\(F=ma\\)" in out
    assert '<a href="https://example.com">x</a>' in out


def test_code_block_and_math_block():
    out = render_blocks([
        {"type": "code", "has_children": False, "id": "c1",
         "code": {"rich_text": [{"type": "text", "plain_text": "def f():\n  return <tag>",
                                 "text": {"content": "def f():\n  return <tag>"},
                                 "annotations": {}}], "language": "python"}},
        {"type": "equation", "has_children": False, "id": "e1",
         "equation": {"expression": "e^{i\\pi}+1=0"}},
    ], MediaCollector(), FakeTree())
    assert '<pre class="ns-code" style="' in out
    assert "background:#f6f7f8" in out
    assert "<code>def f():\n  return &lt;tag&gt;</code></pre>" in out
    assert "\\[e^{i\\pi}+1=0\\]" in out


def test_toggle_and_nested_lists():
    blocks = [
        {"type": "toggle", "has_children": True, "id": "t1",
         "toggle": {"rich_text": [{"type": "text", "plain_text": "Q", "text": {"content": "Q"}, "annotations": {}}]}},
        {"type": "numbered_list_item", "has_children": False, "id": "l1",
         "numbered_list_item": {"rich_text": [{"type": "text", "plain_text": "one", "text": {"content": "one"}, "annotations": {}}]}},
        {"type": "bulleted_list_item", "has_children": True, "id": "l2",
         "bulleted_list_item": {"rich_text": [{"type": "text", "plain_text": "outer", "text": {"content": "outer"}, "annotations": {}}]}},
    ]
    tree = FakeTree({"t1": [
        {"type": "paragraph", "id": "p2", "has_children": False,
         "paragraph": {"rich_text": [{"type": "text", "plain_text": "A", "text": {"content": "A"}, "annotations": {}}]}}
    ], "l2": [
        {"type": "bulleted_list_item", "id": "l3", "has_children": False,
         "bulleted_list_item": {"rich_text": [{"type": "text", "plain_text": "inner", "text": {"content": "inner"}, "annotations": {}}]}},
    ]})
    out = render_blocks(blocks, MediaCollector(), tree)
    assert "<details><summary>Q</summary>" in out and "<p>A</p></details>" in out
    assert "<ol><li>one</li></ol>" in out
    assert "<li>outer<ul><li>inner</li></ul></li>" in out


def test_table_rendering():
    blocks = [{
        "type": "table", "id": "tab", "has_children": True,
        "table": {"table_width": 2, "has_column_header": True, "has_row_header": False},
    }]
    tree = FakeTree({"tab": [
        {"type": "table_row", "id": "r1", "has_children": False,
         "table_row": {"cells": [_rt("A"), _rt("B")]}},
        {"type": "table_row", "id": "r2", "has_children": False,
         "table_row": {"cells": [_rt("1"), _rt("2")]}},
    ]})
    out = render_blocks(blocks, MediaCollector(), tree)
    assert "<thead>" in out
    assert "<th style=" in out and ">A</th><th style=" in out
    assert "<tbody>" in out and "<td style=" in out and ">1</td>" in out
    assert "border-collapse:collapse" in out
    assert "border:1px solid #b8bec5" in out


def test_image_file_becomes_media_placeholder():
    media = MediaCollector()
    blocks = [{
        "type": "image", "id": "1c4c" + "x" * 28, "has_children": False,
        "image": {"type": "file", "file": {"url": "https://s3.example.com/abc.png?ttl=1", "expiry_time": ""}, "caption": []},
    }]
    out = render_blocks(blocks, media, FakeTree())
    assert "<img src=\"@@NSMEDIA:" in out
    assert len(media.files) == 1
    fname = list(media.files)[0]
    assert fname.startswith("ns-1c4c") and fname.endswith(".png")


def test_media_resolve():
    media = MediaCollector()
    ph = media.add("https://x/y.png", "img1.png")
    html = MediaCollector.resolve(f'<img src="{ph}">', {"img1.png": "img1.png"})
    assert '<img src="img1.png">' in html


def _rt(text):
    return [{"type": "text", "plain_text": text, "text": {"content": text},
             "annotations": {"bold": False, "italic": False, "strikethrough": False,
                             "underline": False, "code": False, "color": "default"}}]


def test_canonical_id_and_deck_names():
    assert canonical_page_id("https://app.notion.com/p/Philo-Histoire-1bbf4336b2b480f4a0b6fc07ce4ee5e4") \
        == "1bbf4336b2b480f4a0b6fc07ce4ee5e4"
    assert canonical_page_id(
        "https://app.notion.com/p/Philo-Histoire-1bbf4336-b2b4-80f4-a0b6-fc07ce4ee5e4"
        "?source=copy_link"
    ) == "1bbf4336b2b480f4a0b6fc07ce4ee5e4"
    assert canonical_page_id("1bbf4336-b2b4-80f4-a0b6-fc07ce4ee5e4") == "1bbf4336b2b480f4a0b6fc07ce4ee5e4"
    assert deck_safe("Le bonheur") == "Le bonheur"
    assert deck_safe("a: b::c") == "a  b/c"
    assert deck_safe("") == "Untitled"


def test_token_from_env(tmp_path, monkeypatch):
    monkeypatch.delenv("NOTION_API_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("NOTION_API_TOKEN=secret-token\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert resolve_token({})
    assert "secret" in resolve_token({})
    assert resolve_token({"notion_token": "cfg-token"})


def test_notion_client_uses_configured_token():
    client = NotionClient("secret-token")
    assert client._headers["Authorization"] == "Bearer secret-token"


def test_page_children_are_fetched_once():
    from notion_sync_addon.sync import PageData, SyncResult, _collect_page_children

    class CountingTree:
        def __init__(self):
            self.calls = []

        def children(self, block_id):
            self.calls.append(block_id)
            return []

    tree = CountingTree()
    result = SyncResult()
    data = PageData(title="Page", deck_name="Page")
    _collect_page_children(
        "page-id", object(), tree, MediaCollector(), data, result
    )
    assert tree.calls == ["page-id"]


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
    assert MODEL_CSS == (
        ".card { font-family: arial; font-size: 20px; line-height: 1.5; "
        "text-align: center; color: black; background-color: white; }"
    )


def test_existing_model_css_is_updated():
    class Models:
        def __init__(self):
            self.saved = []

        def by_name(self, name):
            return {"css": ".card { text-align: left; }"}

        def save(self, model):
            self.saved.append(model)

    class Collection:
        def __init__(self):
            self.models = Models()

    col = Collection()
    model = _get_or_create_model(col)
    assert model["css"] == MODEL_CSS
    assert col.models.saved == [model]


def test_empty_cards_are_enabled_by_default():
    class Client:
        def get_database(self, database_id):
            return {"title": [{"plain_text": "Table"}], "data_sources": [{"id": "ds"}]}

        def query_data_source(self, data_source_id):
            return [{"id": "row-1", "properties": {"Name": {"type": "title", "title": []}}}]

    class Tree:
        def children(self, block_id):
            return []

    result = SyncResult()
    result.config = {}
    table = _fetch_table(Client(), Tree(), {"id": "db-1"}, MediaCollector(), "Page", result)
    assert table is not None
    assert len(table.rows) == 1
    assert table.rows[0].front == ""

    result.config = {"include_empty_cards": False}
    table = _fetch_table(Client(), Tree(), {"id": "db-1"}, MediaCollector(), "Page", result)
    assert table is not None
    assert table.rows == []


# --------------------------------------------------------------------- #
# last_edited_time row cache
# --------------------------------------------------------------------- #

EDITED = "2026-11-04T10:51:00.000Z"
EDITED_TS = 1793789460.0  # EDITED as unix time


def test_unchanged_requires_same_edit_time_full_minute_and_skew_margin():
    now = EDITED_TS + 3600
    fresh = RowState(EDITED, EDITED_TS + 600)
    assert is_unchanged(fresh, EDITED, "k", now)
    # fetched during the edit minute: a later edit in that minute is invisible
    assert not is_unchanged(RowState(EDITED, EDITED_TS + 30), EDITED, "k", now)
    # this computer's clock may run a few minutes ahead of Notion's
    assert not is_unchanged(RowState(EDITED, EDITED_TS + 120), EDITED, "k", now)
    assert not is_unchanged(fresh, "2026-11-04T11:45:00.000Z", "k", now)
    assert not is_unchanged(None, EDITED, "k", now)
    assert not is_unchanged(fresh, "", "k", now)
    # content that can change without bumping last_edited_time
    assert not is_unchanged(RowState(EDITED, EDITED_TS + 600, volatile=True), EDITED, "k", now)
    # fetched "in the future": the clock was wrong then
    assert not is_unchanged(RowState(EDITED, now + 3600), EDITED, "k", now)


def test_cached_rows_expire_after_7_to_14_days():
    from notion_sync_addon.sync import AGE_SPREAD_SECONDS, MAX_AGE_SECONDS, max_age

    ages = [max_age(f"row{i}") for i in range(200)]
    assert all(MAX_AGE_SECONDS <= a <= MAX_AGE_SECONDS + AGE_SPREAD_SECONDS for a in ages)
    assert max(ages) - min(ages) > AGE_SPREAD_SECONDS / 2  # spread, not one day
    state = RowState(EDITED, EDITED_TS + 600)
    assert is_unchanged(state, EDITED, "row1", EDITED_TS + 600 + max_age("row1") - 1)
    assert not is_unchanged(state, EDITED, "row1", EDITED_TS + 600 + max_age("row1") + 1)


class _RowsClient:
    def __init__(self, rows):
        self.rows = rows

    def get_database(self, database_id):
        return {"title": [{"plain_text": "Defs"}], "data_sources": [{"id": "ds"}]}

    def query_data_source(self, data_source_id):
        return self.rows


def _row(row_id, title, edited):
    return {"id": row_id, "last_edited_time": edited, "url": "",
            "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]}}}


@pytest.fixture
def frozen_now(monkeypatch):
    """Pin the sync's clock an hour after EDITED (cache entries stay fresh)."""
    import notion_sync_addon.sync as sync_mod

    monkeypatch.setattr(sync_mod.time, "time", lambda: EDITED_TS + 3600)
    return EDITED_TS + 3600


def test_unchanged_rows_skip_content_download(frozen_now):
    fetched = []

    class Tree:
        def children(self, block_id):
            fetched.append(block_id)
            return [{"type": "paragraph", "paragraph": {"rich_text": [
                {"plain_text": "body", "annotations": {}, "type": "text",
                 "text": {"content": "body"}}]}}]

    later = "2026-11-04T11:45:00.000Z"
    client = _RowsClient([_row("aaa", "Ideologie", EDITED), _row("bbb", "Racisme", later)])
    result = SyncResult()
    result.known = {"aaa": RowState(EDITED, EDITED_TS + 600, "fp"),
                    "bbb": RowState(EDITED, EDITED_TS + 600, "fp")}
    table = _fetch_table(client, Tree(), {"id": "db"}, MediaCollector(), "Page", result, "page")

    assert fetched == ["bbb"]  # only the edited row is downloaded
    by_id = {r.source_id: r for r in table.rows}
    assert by_id["aaa"].unchanged and by_id["aaa"].back_html == ""
    assert not by_id["bbb"].unchanged and "body" in by_id["bbb"].back_html
    assert result.fetched["aaa"] == RowState(EDITED, EDITED_TS + 600, "fp", "page", "db")
    assert result.fetched["bbb"].edited == later
    assert (result.fetched["bbb"].page_id, result.fetched["bbb"].db_id) == ("page", "db")
    assert not result.incomplete


def test_failed_table_query_marks_result_incomplete():
    from notion_sync_addon.notion_api import NotionError

    class Client(_RowsClient):
        def query_data_source(self, data_source_id):
            raise NotionError("boom")

    result = SyncResult()
    _fetch_table(Client([]), FakeTree(), {"id": "db"}, MediaCollector(), "Page", result)
    assert result.incomplete  # apply_sync then skips removing notes


def test_unchanged_page_reuses_table_list_and_data_source(frozen_now):
    from notion_sync_addon.sync import fetch_pages

    calls = []

    class Client:
        def get_page(self, page_id):
            calls.append("page")
            return {"last_edited_time": EDITED,
                    "properties": {"t": {"type": "title", "title": [{"plain_text": "Hist"}]}}}

        def get_database(self, database_id):
            calls.append("database")
            return {"title": [{"plain_text": "Defs"}], "data_sources": [{"id": "ds-1"}]}

        def query_data_source(self, data_source_id):
            calls.append("query:" + data_source_id)
            return []

    class Tree:
        def children(self, block_id):
            calls.append("blocks")
            return [{"id": "db-1", "type": "child_database", "child_database": {"title": "Defs"}}]

    import notion_sync_addon.sync as sync_mod

    page_id = "1f2e3d4c5b6a79881726354433221100"
    orig_client, orig_tree = sync_mod.NotionClient, sync_mod.BlockTree
    sync_mod.NotionClient = lambda token: Client()
    sync_mod.BlockTree = lambda client: Tree()
    try:
        cfg = {"notion_token": "t", "notion_pages": [page_id]}
        first = fetch_pages(cfg)
        assert calls == ["page", "blocks", "database", "query:ds-1"]
        structure = {"pages": dict(first.pages_out), "dbs": dict(first.dbs_out)}
        # pretend the first fetch happened well after the page's last edit
        structure["pages"][page_id][1] = EDITED_TS + 600
        calls.clear()
        second = fetch_pages(cfg, {}, None, structure)
        # no block walk, no database lookup: just the page and the row query
        assert calls == ["page", "query:ds-1"]
        assert [t.title for p in second.pages for t in p.tables] == ["Defs"]
        first.cleanup()
        second.cleanup()
    finally:
        sync_mod.NotionClient, sync_mod.BlockTree = orig_client, orig_tree


# --------------------------------------------------------------------- #
# Notion API helpers
# --------------------------------------------------------------------- #


def test_retry_after_parsing():
    import time as _time
    from email.utils import formatdate

    from notion_sync_addon.notion_api import retry_delay

    assert retry_delay("3", 2.0) == 3.0
    assert retry_delay(None, 2.0) == 2.0
    assert retry_delay("soon", 2.0) == 2.0
    later = retry_delay(formatdate(_time.time() + 10, usegmt=True), 2.0)
    assert 5 <= later <= 11
    assert retry_delay(formatdate(_time.time() - 100, usegmt=True), 2.0) == 0.0


def test_download_has_a_timeout(monkeypatch):
    import urllib.request

    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"x"

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert NotionClient("t").download_file("https://example.com/f") == b"x"
    assert seen["timeout"] and seen["timeout"] > 0


def test_invalid_json_is_a_notion_error(monkeypatch):
    from notion_sync_addon.notion_api import NotionError

    class Resp:
        status = 200

        def read(self):
            return b"<html>oops"

        def getheader(self, name):
            return None

    class Conn:
        def request(self, *a, **k):
            pass

        def getresponse(self):
            return Resp()

    client = NotionClient("t")
    monkeypatch.setattr(client, "_connection", lambda: Conn())
    with pytest.raises(NotionError):
        client.get_page("p")


def test_fetch_cleans_temp_dir_on_unexpected_error(monkeypatch):
    import notion_sync_addon.sync as sync_mod

    made = []
    real_mkdtemp = sync_mod.tempfile.mkdtemp

    def mkdtemp(**kw):
        made.append(real_mkdtemp(**kw))
        return made[-1]

    class Boom:
        def __init__(self, token):
            pass

        def get_page(self, page_id):
            raise ValueError("not a NotionError")

    monkeypatch.setattr(sync_mod.tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr(sync_mod, "NotionClient", Boom)
    with pytest.raises(ValueError):
        sync_mod.fetch_pages({"notion_token": "t", "notion_pages": ["a" * 32]})
    assert made and not os.path.exists(made[0])


# --------------------------------------------------------------------- #
# Cache file
# --------------------------------------------------------------------- #


def test_cache_path_is_per_profile_and_safe():
    from notion_sync_addon.sync import cache_path

    a = cache_path("/addon", "User 1")
    b = cache_path("/addon", "User_1")
    c = cache_path("/addon", "../../evil\\name")
    assert a != b
    assert os.path.dirname(a) == os.path.join("/addon", "user_files")
    assert os.path.dirname(c) == os.path.join("/addon", "user_files")
    assert ".." not in os.path.basename(c) and "\\" not in os.path.basename(c)


def test_cache_file_roundtrip_corrupt_and_unchanged(tmp_path):
    from notion_sync_addon.sync import Cache, load_cache, save_cache

    path = str(tmp_path / "user_files" / "cache.json")
    assert load_cache(path).rows == {}  # missing
    cache = Cache(rows={"r": RowState(EDITED, EDITED_TS, "fp", "p", "d", True)},
                  pages={"p": [EDITED, EDITED_TS, [["d", "Defs"]]]}, dbs={"d": "ds"})
    assert save_cache(path, cache)
    loaded = load_cache(path)
    assert loaded.rows == cache.rows and loaded.pages == cache.pages and loaded.dbs == cache.dbs
    assert not save_cache(path, loaded)  # unchanged: not rewritten
    assert os.listdir(tmp_path / "user_files") == ["cache.json"]  # no temp file left
    for junk in ("{not json", "[]", '{"v": -1, "rows": {}}',
                 '{"v": 2, "rows": {"r": [1]}, "pages": {"p": [1, "x", 3]}}'):
        with open(path, "w", encoding="utf-8") as f:
            f.write(junk)
        bad = load_cache(path)
        assert bad.rows == {} and bad.pages == {}


# --------------------------------------------------------------------- #
# End to end against a real Anki collection
# --------------------------------------------------------------------- #

OLD = "2026-01-01T00:00:00.000Z"


def _para(text):
    return {"id": "blk", "type": "paragraph", "has_children": False,
            "paragraph": {"rich_text": _rt(text)}}


class FakeNotion:
    """In-memory Notion: pages -> tables -> rows. Records row downloads."""

    def __init__(self):
        self.pages = {}  # page id -> {"edited", "title", "tables": [db id]}
        self.tables = {}  # db id -> {"title", "rows": {row id: [title, edited, blocks]}}
        self.fail = set()  # ("page"|"db"|"query"|"row", id)
        self.media_fail = False
        self.downloads = []

    def __call__(self, token):  # stands in for the NotionClient class
        return self

    def get_page(self, pid):
        if ("page", pid) in self.fail:
            raise NotionError(f"page {pid} failed")
        page = self.pages[pid]
        return {"last_edited_time": page["edited"],
                "properties": {"t": {"type": "title", "title": [{"plain_text": page["title"]}]}}}

    def children(self, bid):
        if bid in self.pages:
            return [{"id": db, "type": "child_database",
                     "child_database": {"title": self.tables[db]["title"]}}
                    for db in self.pages[bid]["tables"]]
        for table in self.tables.values():
            if bid in table["rows"]:
                if ("row", bid) in self.fail:
                    raise NotionError("row failed")
                self.downloads.append(bid)
                return table["rows"][bid][2]
        return []

    def get_database(self, db):
        if ("db", db) in self.fail:
            raise NotionError("GET database 503")
        return {"title": [{"plain_text": self.tables[db]["title"]}],
                "data_sources": [{"id": "ds-" + db}]}

    def query_data_source(self, ds):
        db = ds[3:]
        if ("query", db) in self.fail:
            raise NotionError("query 503")
        return [{"id": rid, "last_edited_time": r[1], "url": "",
                 "properties": {"Name": {"type": "title", "title": [{"plain_text": r[0]}]}}}
                for rid, r in self.tables[db]["rows"].items()]

    def download_file(self, url):
        if self.media_fail:
            raise OSError("timed out")
        return b"\x89PNG\r\n\x1a\n" + url.encode()


P1, P2 = "1" * 32, "2" * 32
DB1, DB2, DB3 = "d1" + "0" * 30, "d2" + "0" * 30, "d3" + "0" * 30


def _rid(n):
    return f"r{n:03d}".ljust(32, "0")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A temp collection, a fake Notion with two pages / three tables, and
    ``env.sync()`` running prepare -> fetch -> apply like the add-on does."""
    collection = pytest.importorskip("anki.collection")
    import notion_sync_addon.sync as sync_mod

    notion = FakeNotion()
    notion.pages[P1] = {"edited": OLD, "title": "Hist", "tables": [DB1]}
    notion.pages[P2] = {"edited": OLD, "title": "Philo", "tables": [DB2, DB3]}
    notion.tables[DB1] = {"title": "Defs", "rows": {
        _rid(i): [f"h{i}", OLD, [_para(f"hist {i}")]] for i in range(3)}}
    notion.tables[DB2] = {"title": "Notions", "rows": {
        _rid(10 + i): [f"p{i}", OLD, [_para(f"philo {i}")]] for i in range(3)}}
    notion.tables[DB3] = {"title": "Auteurs", "rows": {
        _rid(20 + i): [f"a{i}", OLD, [_para(f"author {i}")]] for i in range(2)}}
    monkeypatch.setattr(sync_mod, "NotionClient", notion)
    monkeypatch.setattr(sync_mod, "BlockTree", lambda client: client)
    col = collection.Collection(str(tmp_path / "col.anki2"))
    path = str(tmp_path / "user_files" / "cache.json")

    class Env:
        pass

    e = Env()
    e.col, e.notion, e.path, e.sync_mod = col, notion, path, sync_mod
    e.cfg = {"notion_token": "t", "notion_pages": [P1, P2]}

    def run(full=False, between=None):
        prep = sync_mod.prepare_sync(col, path, full)
        result = sync_mod.fetch_pages(e.cfg, prep.known, None, prep.structure, prep.previous)
        result.cache_path = path
        if between:
            between()
        stats = sync_mod.apply_sync(col, result)
        e.result = result
        return stats

    def note(row_id):
        nids = col.find_notes(f"Source:{row_id}")
        return col.get_note(nids[0]) if nids else None

    e.sync, e.note = run, note
    e.count = lambda: len(col.find_notes('"note:Notion Sync Card"'))
    yield e
    col.close()


def test_first_sync_then_noop_is_cached_and_not_undoable(env):
    first = env.sync()
    assert first["added"] == 8 and env.count() == 8
    assert env.col.undo_status().undo == "Notion Sync"
    env.notion.downloads.clear()
    env.col.add_custom_undo_entry("Previous action")
    second = env.sync()
    assert second == {"added": 0, "updated": 0, "removed": 0, "unchanged": 8, "media": 0}
    assert env.notion.downloads == []
    assert env.result.changes is None
    assert env.col.undo_status().undo == "Previous action"  # no new undo entry


def test_large_sync_is_still_one_undo_step(env):
    """Anki keeps ~30 undo steps; 100 note changes must still merge into one."""
    env.notion.tables[DB1]["rows"].update(
        {_rid(100 + i): [f"x{i}", OLD, [_para(f"x {i}")]] for i in range(100)})
    env.col.add_custom_undo_entry("Previous action")
    assert env.sync()["added"] == 108
    assert env.col.undo_status().undo == "Notion Sync"
    env.col.undo()
    assert env.count() == 0
    assert env.col.undo_status().undo == "Previous action"


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
    assert env.sync_mod.load_cache(env.path).pages[P2][2] == [[DB2, "Notions"], [DB3, "Auteurs"]]
    env.notion.fail = set()
    stats = env.sync()
    assert stats["removed"] == 0 and env.count() == 8


def test_removal_is_scoped_to_the_failed_table(env):
    env.sync()
    del env.notion.tables[DB1]["rows"][_rid(0)]  # deleted in a healthy table
    del env.notion.tables[DB2]["rows"][_rid(10)]  # deleted in the failing table
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
    env.notion.tables[DB1]["rows"][_rid(0)] = ["h0", "2026-01-05T00:00:00.000Z", [_para("v2")]]
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


def test_deleted_note_is_recreated(env):
    env.sync()
    env.col.remove_notes([env.note(_rid(2)).id])
    assert env.sync()["added"] == 1 and env.note(_rid(2)) is not None


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


def test_failed_media_is_written_but_retried(env):
    env.sync()
    image = {"id": "1a2b" + "0" * 28, "type": "image", "has_children": False,
             "image": {"type": "file", "file": {"url": "https://s3/pic.png?x=1"}, "caption": []}}
    env.notion.tables[DB1]["rows"][_rid(0)] = ["h0", "2026-01-05T00:00:00.000Z", [image]]
    env.notion.media_fail = True
    stats = env.sync()
    assert stats["updated"] == 1 and not env.result.incomplete
    assert "missing media" in env.note(_rid(0))["Back"]
    assert any("media" in w for w in env.result.warnings)
    env.notion.media_fail = False
    env.notion.downloads.clear()
    stats = env.sync()
    assert env.notion.downloads == [_rid(0)] and stats["updated"] == 1
    assert "missing media" not in env.note(_rid(0))["Back"]
    env.notion.downloads.clear()
    env.sync()
    assert env.notion.downloads == []


def test_media_from_an_earlier_sync_is_reused_when_download_fails(env):
    image = {"id": "1a2b" + "0" * 28, "type": "image", "has_children": False,
             "image": {"type": "file", "file": {"url": "https://s3/pic.png?x=1"}, "caption": []}}
    env.notion.tables[DB1]["rows"][_rid(0)] = ["h0", OLD, [image]]
    env.sync()
    back = env.note(_rid(0))["Back"]
    assert "missing media" not in back
    env.notion.tables[DB1]["rows"][_rid(0)][1] = "2026-01-05T00:00:00.000Z"
    env.notion.media_fail = True
    env.sync()
    assert env.note(_rid(0))["Back"] == back


def test_synced_block_copies_are_always_downloaded(env):
    synced = {"id": "sb", "type": "synced_block", "has_children": True,
              "synced_block": {"synced_from": {"block_id": "elsewhere"}}}
    env.notion.tables[DB1]["rows"][_rid(0)][2] = [_para("x"), synced]
    env.sync()
    env.notion.downloads.clear()
    env.sync()
    assert env.notion.downloads == [_rid(0)]


def test_full_resync_bypasses_every_cache(env):
    env.sync()
    env.notion.downloads.clear()
    stats = env.sync(full=True)
    assert stats["unchanged"] == 0 and len(env.notion.downloads) == 8


def test_legacy_config_cache_is_removed(env):
    env.col.set_config("notionSync.rowState", {"v": 1, "rows": {}})
    env.sync()
    assert env.col.get_config("notionSync.rowState", None) is None


def test_note_type_css_change_is_one_undo_step(env):
    env.sync()
    model = env.col.models.by_name("Notion Sync Card")
    model["css"] = ".card {}"
    env.col.models.save(model)
    env.col.add_custom_undo_entry("Previous action")
    env.sync()
    assert env.col.undo_status().undo == "Notion Sync"


# --------------------------------------------------------------------- #
# Add-on orchestration (__init__.py) with a fake mw and synchronous ops
# --------------------------------------------------------------------- #


class _Hooks:
    def __init__(self):
        self.calls = []

    def operation_did_execute(self, changes, initiator):
        self.calls.append("operation_did_execute")

    def state_did_reset(self):
        self.calls.append("state_did_reset")


@pytest.fixture
def addon(env, monkeypatch, tmp_path):
    import notion_sync_addon as addon_mod

    queue = []

    class TaskMan:
        defer = False

        def run_on_main(self, fn):
            queue.append(fn) if self.defer else fn()

    class PM:
        name = "User 1"
        auth = "token"

        def sync_auth(self):
            return self.auth

    class MediaSyncer:
        def is_syncing(self):
            return False

    class FakeMw:
        col = env.col
        pm = PM()
        taskman = TaskMan()
        media_syncer = MediaSyncer()
        sync_clicks = 0

        class addonManager:
            @staticmethod
            def getConfig(name):
                return env.cfg

        class toolbar:
            class web:
                @staticmethod
                def eval(js):
                    pass

        def on_sync_button_clicked(self):
            FakeMw.sync_clicks += 1
            addon_mod._on_ankiweb_sync_will_start()
            addon_mod._on_ankiweb_sync_finished()

    class FakeQueryOp:
        def __init__(self, *, parent, op, success):
            self.op, self.success, self.fail = op, success, None

        def failure(self, fn):
            self.fail = fn
            return self

        def without_collection(self):
            return self

        def run_in_background(self):
            try:
                value = self.op(FakeMw.col)
            except Exception as exc:  # noqa: BLE001
                self.fail(exc)
                return
            self.success(value)

    shown = []
    finished = []
    statuses = []
    monkeypatch.setattr(addon_mod, "mw", FakeMw(), raising=False)
    monkeypatch.setattr(addon_mod, "QueryOp", FakeQueryOp, raising=False)
    monkeypatch.setattr(addon_mod, "tooltip", lambda msg, **k: shown.append(msg), raising=False)
    monkeypatch.setattr(addon_mod, "showWarning", lambda msg, **k: shown.append(msg), raising=False)
    monkeypatch.setattr(addon_mod, "_op_finished", finished.append)
    real_set_status = addon_mod._set_status
    monkeypatch.setattr(addon_mod, "_set_status", lambda text=None: (
        statuses.append(text), real_set_status(text)))
    monkeypatch.setattr(addon_mod, "ADDON_DIR", str(tmp_path / "addon"))
    monkeypatch.setitem(addon_mod._state, "busy", False)
    for key in ("followup_pending", "followup_running", "ankiweb_failed", "closing"):
        monkeypatch.setitem(addon_mod._state, key, False)

    class A:
        pass

    a = A()
    a.mod, a.mw, a.shown, a.finished, a.statuses, a.queue = (
        addon_mod, FakeMw, shown, finished, statuses, queue)
    return a


def test_run_sync_end_to_end_without_modal_progress(addon, env):
    addon.mod.run_sync(manual=True)
    assert not addon.mod._state["busy"] and env.count() == 8
    assert len(addon.finished) == 1  # undo menu / screens refreshed once
    assert os.path.exists(env.sync_mod.cache_path(str(addon.mod.ADDON_DIR), "User 1"))
    addon.mod.run_sync(manual=True)
    assert len(addon.finished) == 1  # nothing changed: no undo step, no refresh
    assert any("8 unchanged" in m for m in addon.shown)


def test_every_failure_resets_busy(addon, env, monkeypatch):
    def boom(*a, **k):
        raise ValueError("unexpected")

    for target in ("fetch_pages", "prepare_sync", "apply_sync"):
        with monkeypatch.context() as m:
            m.setattr(env.sync_mod, target, boom)
            addon.mod.run_sync(manual=False)
        assert not addon.mod._state["busy"], target
        assert addon.statuses[-1] is None
    with monkeypatch.context() as m:  # a success callback that raises
        m.setattr(addon.mod, "_report", boom)
        addon.mod.run_sync(manual=False)
    assert not addon.mod._state["busy"]


def test_closing_profile_stops_before_import(addon, env, monkeypatch):
    real = env.sync_mod.fetch_pages

    def fetch_then_close(*a, **k):
        result = real(*a, **k)
        addon.mod._state["closing"] = True
        return result

    monkeypatch.setattr(env.sync_mod, "fetch_pages", fetch_then_close)
    addon.mod.run_sync(manual=False)
    assert env.count() == 0 and not addon.mod._state["busy"]


def test_late_progress_of_an_old_sync_is_ignored(addon, env):
    addon.mw.taskman.defer = True
    addon.mod.run_sync(manual=False, full=True)
    old = list(addon.queue)
    addon.queue.clear()
    assert old  # progress callbacks still queued
    addon.mod.run_sync(manual=False)  # newer sync starts
    addon.mod._state["busy"] = True  # ... and is still running
    addon.statuses.clear()
    for fn in old:
        fn()
    assert addon.statuses == []
    for fn in addon.queue:
        fn()
    assert addon.statuses  # the current sync's progress still shows


def test_ankiweb_followup_runs_once_and_never_loops(addon, env, monkeypatch):
    env.cfg["sync_with_ankiweb"] = True
    # skip the AnkiWeb status request; upload straight away
    monkeypatch.setattr(addon.mod, "_upload_to_ankiweb", addon.mod._start_followup_sync)
    addon.mod._on_ankiweb_sync_will_start()
    addon.mod._on_ankiweb_sync_finished()
    assert env.count() == 8
    assert addon.mw.sync_clicks == 1  # one upload, no Notion sync after it
    assert not addon.mod._state["followup_running"]
    assert not addon.mod._state["followup_pending"]


def test_failed_ankiweb_sync_does_not_start_notion(addon, env):
    env.cfg["sync_with_ankiweb"] = True
    addon.mod._on_ankiweb_sync_will_start()
    addon.mod._state["ankiweb_failed"] = True  # what the error probe records
    addon.mod._on_ankiweb_sync_finished()
    assert env.count() == 0


def test_followup_flags_cannot_get_stuck(addon, env):
    env.cfg["sync_with_ankiweb"] = True
    # Anki shows the media log instead of syncing: no hooks fire
    addon.mw.on_sync_button_clicked = lambda self=None: None
    addon.mod._start_followup_sync()
    assert not addon.mod._state["followup_pending"]
    # a follow-up whose sync_did_finish never came does not eat the next sync
    addon.mod._state["followup_running"] = True
    addon.mod._on_ankiweb_sync_will_start()
    addon.mod._on_ankiweb_sync_finished()
    assert env.count() == 8


def test_no_followup_without_saved_login(addon, env):
    addon.mw.pm.auth = None
    addon.mod._start_followup_sync()
    assert addon.mw.sync_clicks == 0


def test_sync_error_probe_flags_failure(monkeypatch):
    import types

    import notion_sync_addon as addon_mod

    fake = types.ModuleType("aqt.sync")
    calls = []
    fake.handle_sync_error = lambda mw, err: calls.append(err)
    pkg = types.ModuleType("aqt")
    pkg.sync = fake
    monkeypatch.setitem(__import__("sys").modules, "aqt", pkg)
    monkeypatch.setitem(__import__("sys").modules, "aqt.sync", fake)
    monkeypatch.setitem(addon_mod._state, "ankiweb_failed", False)
    addon_mod._install_sync_error_probe()
    addon_mod._install_sync_error_probe()  # idempotent
    fake.handle_sync_error(None, "network down")
    assert calls == ["network down"] and addon_mod._state["ankiweb_failed"]
