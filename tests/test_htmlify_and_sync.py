# -*- coding: utf-8 -*-
import os

from notion_sync_addon.htmlify import MediaCollector, render_blocks
from notion_sync_addon.sync import (
    _get_or_create_model,
    _remove_obsolete,
    canonical_page_id,
    deck_safe,
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
         "code": {"rich_text": [{"type": "text", "plain_text": "def f():\n  pass",
                                 "text": {"content": "def f():\n  pass"},
                                 "annotations": {}}], "language": "python"}},
        {"type": "equation", "has_children": False, "id": "e1",
         "equation": {"expression": "e^{i\\pi}+1=0"}},
    ], MediaCollector(), FakeTree())
    assert "<pre" in out and "<code>def f():<br>  pass</code></pre>" not in out  # code is escaped, not <br>
    assert "def f():\n  pass" in out
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
    assert "<thead>" in out and "<th>A</th><th>B</th>" in out
    assert "<tbody>" in out and "<td>1</td>" in out


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
