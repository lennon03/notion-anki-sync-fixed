# -*- coding: utf-8 -*-
"""Talking to Notion: the client, page links and the token."""

import pytest

from notion_sync_addon.notion_api import NotionClient, NotionError
from notion_sync_addon.sync import canonical_page_id, deck_safe, resolve_token


def test_canonical_id_and_deck_names():
    assert (
        canonical_page_id(
            "https://app.notion.com/p/Philo-Histoire-"
            "1bbf4336b2b480f4a0b6fc07ce4ee5e4"
        )
        == "1bbf4336b2b480f4a0b6fc07ce4ee5e4"
    )
    assert (
        canonical_page_id(
            "https://app.notion.com/p/Philo-Histoire-"
            "1bbf4336-b2b4-80f4-a0b6-fc07ce4ee5e4"
            "?source=copy_link"
        )
        == "1bbf4336b2b480f4a0b6fc07ce4ee5e4"
    )
    assert (
        canonical_page_id("1bbf4336-b2b4-80f4-a0b6-fc07ce4ee5e4")
        == "1bbf4336b2b480f4a0b6fc07ce4ee5e4"
    )
    assert deck_safe("Le bonheur") == "Le bonheur"
    assert deck_safe("a: b::c") == "a  b/c"
    assert deck_safe("") == "Untitled"


ID = "1bbf4336b2b480f4a0b6fc07ce4ee5e4"


def test_page_id_is_lowercase_however_it_was_pasted():
    assert canonical_page_id(ID.upper()) == ID
    assert (
        canonical_page_id(f"https://www.notion.so/My-Page-{ID.upper()}") == ID
    )


def test_text_that_only_holds_hex_letters_is_not_a_page_id():
    with pytest.raises(NotionError):
        canonical_page_id("a decade of faded facade: bad cafe, dead beef")


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


def test_download_has_a_timeout(monkeypatch, tmp_path):
    import urllib.request

    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __init__(self):
            self.chunks = [b"x", b"y"]

        def read(self, size=-1):
            return self.chunks.pop(0) if self.chunks else b""

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    path = tmp_path / "f"
    NotionClient("t").download_file("https://example.com/f", str(path))
    assert path.read_bytes() == b"xy"
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
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(NotionError) as caught:
        client.get_page("p")
    assert caught.value.transient


def test_a_query_stops_once_it_has_more_rows_than_asked_for(monkeypatch):
    client = NotionClient("token")
    asked = []

    def request(method, path, data=None):
        asked.append(data)
        rows = [{"id": str(len(asked))}] * 100
        return {"results": rows, "has_more": True, "next_cursor": "next"}

    monkeypatch.setattr(client, "_request", request)
    rows = client.query_data_source("ds", stop_after=150)
    assert len(rows) == 200 and len(asked) == 2


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
