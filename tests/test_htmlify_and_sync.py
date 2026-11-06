# -*- coding: utf-8 -*-
import os

from notion_sync_addon.htmlify import MediaCollector, render_blocks
from notion_sync_addon.notion_api import NotionClient
from notion_sync_addon.sync import (
    MODEL_CSS,
    SyncResult,
    _get_or_create_model,
    _fetch_table,
    _remove_obsolete,
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


def test_obsolete_cleanup_keeps_manual_cards():
    class Note:
        def __init__(self, source):
            self.fields = {"Source": source}

        def __getitem__(self, key):
            return self.fields[key]

    class Collection:
        def __init__(self):
            self.notes = {1: Note("stale-source"), 2: Note("")}
            self.removed = []

        def find_notes(self, query):
            return list(self.notes)

        def get_note(self, note_id):
            return self.notes[note_id]

        def remove_notes(self, note_ids):
            self.removed.extend(note_ids)

    col = Collection()
    assert _remove_obsolete(col, {"current-source"}) == 1
    assert col.removed == [1]


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


def test_unchanged_requires_same_edit_time_and_full_minute():
    assert is_unchanged((EDITED, EDITED_TS + 120), EDITED)
    # fetched during the edit minute: a later edit in that minute is invisible
    assert not is_unchanged((EDITED, EDITED_TS + 30), EDITED)
    assert not is_unchanged((EDITED, EDITED_TS + 120), "2026-11-04T11:45:00.000Z")
    assert not is_unchanged(None, EDITED)
    assert not is_unchanged((EDITED, EDITED_TS + 120), "")


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


def test_unchanged_rows_skip_content_download():
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
    result.known = {"aaa": (EDITED, EDITED_TS + 600), "bbb": (EDITED, EDITED_TS + 600)}
    table = _fetch_table(client, Tree(), {"id": "db"}, MediaCollector(), "Page", result)

    assert fetched == ["bbb"]  # only the edited row is downloaded
    by_id = {r.source_id: r for r in table.rows}
    assert by_id["aaa"].unchanged and by_id["aaa"].back_html == ""
    assert not by_id["bbb"].unchanged and "body" in by_id["bbb"].back_html
    assert result.fetched["aaa"] == (EDITED, EDITED_TS + 600)
    assert result.fetched["bbb"][0] == later
    assert not result.incomplete


def test_failed_table_query_marks_result_incomplete():
    from notion_sync_addon.notion_api import NotionError

    class Client(_RowsClient):
        def query_data_source(self, data_source_id):
            raise NotionError("boom")

    result = SyncResult()
    _fetch_table(Client([]), FakeTree(), {"id": "db"}, MediaCollector(), "Page", result)
    assert result.incomplete  # apply_sync then skips removing notes


def test_unchanged_page_reuses_table_list_and_data_source():
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
