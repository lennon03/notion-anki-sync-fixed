# -*- coding: utf-8 -*-
"""Notion client and id parsing points of docs/review.md (C5, C9)."""

import pytest

from notion_sync_addon.notion_api import NotionClient, NotionError
from notion_sync_addon.sync import canonical_page_id

ID = "1bbf4336b2b480f4a0b6fc07ce4ee5e4"


def test_page_id_is_lowercase_however_it_was_pasted():
    assert canonical_page_id(ID.upper()) == ID
    assert (
        canonical_page_id(f"https://www.notion.so/My-Page-{ID.upper()}") == ID
    )


def test_text_that_only_holds_hex_letters_is_not_a_page_id():
    with pytest.raises(NotionError):
        canonical_page_id("a decade of faded facade: bad cafe, dead beef")


def test_block_children_stop_when_notion_sends_no_cursor(monkeypatch):
    client = NotionClient("token")
    answers = [
        {"results": [{"id": "a"}], "has_more": True, "next_cursor": "c1"},
        {"results": [{"id": "b"}], "has_more": True, "next_cursor": None},
    ]
    asked = []

    def request(method, path, data=None):
        asked.append(path)
        return answers.pop(0)

    monkeypatch.setattr(client, "_request", request)
    assert [b["id"] for b in client.get_block_children("x")] == ["a", "b"]
    assert asked[1].endswith("&start_cursor=c1")


class _Answer:
    def __init__(self, body):
        self.status = 200
        self.body = body

    def read(self):
        return self.body

    def getheader(self, name):
        return None


class _Conn:
    def __init__(self, bodies):
        self.bodies = bodies
        self.calls = 0

    def request(self, *a, **k):
        self.calls += 1

    def getresponse(self):
        return _Answer(self.bodies.pop(0))


def test_an_answer_that_is_not_json_is_retried(monkeypatch):
    client = NotionClient("token")
    conn = _Conn([b"<html>proxy page", b'{"id": "p"}'])
    monkeypatch.setattr(client, "_connection", lambda: conn)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert client.get_page("p") == {"id": "p"}
    assert conn.calls == 2


def test_an_answer_that_is_never_json_fails_after_the_retries(monkeypatch):
    from notion_sync_addon.notion_api import MAX_RETRIES

    client = NotionClient("token")
    conn = _Conn([b"<html>"] * (MAX_RETRIES + 1))
    monkeypatch.setattr(client, "_connection", lambda: conn)
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(NotionError) as caught:
        client.get_page("p")
    assert caught.value.transient
    assert conn.calls == MAX_RETRIES + 1


def test_a_long_media_name_keeps_its_extension():
    from notion_sync_addon.htmlify import MAX_FILE_NAME, _file_safe

    name = _file_safe("ns-abc-" + "x" * 200 + ".PNG")
    assert len(name) == MAX_FILE_NAME
    assert name.endswith("x.png")
    assert name.startswith("ns-abc-")


def test_a_short_media_name_is_unchanged_and_a_long_dotted_one_is_cut():
    from notion_sync_addon.htmlify import MAX_FILE_NAME, _file_safe

    assert _file_safe("ns-abc-My File.pdf") == "ns-abc-my_file.pdf"
    name = _file_safe("a." + "b" * 200)  # no real extension to keep
    assert len(name) == MAX_FILE_NAME and name.startswith("a.bbb")


def test_rendered_blocks_name_their_parts():
    from notion_sync_addon.fetch import Rendered, _render_blocks
    from notion_sync_addon.htmlify import MediaCollector
    from notion_sync_addon.rows import SyncResult

    para = {
        "id": "p",
        "type": "paragraph",
        "has_children": False,
        "paragraph": {
            "rich_text": [
                {"type": "text", "plain_text": "x", "text": {"content": "x"}}
            ]
        },
    }
    done = _render_blocks(
        [para], "Q", "", MediaCollector(), None, SyncResult()
    )
    assert done == Rendered("basic", "<p>x</p>", "")
