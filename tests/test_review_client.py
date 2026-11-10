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
