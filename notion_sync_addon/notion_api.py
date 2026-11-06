# -*- coding: utf-8 -*-
"""Official Notion REST API client (Notion-Version 2025-09-03).

Only read endpoints are used: pages, block children, databases,
data sources and data source queries. Handles pagination and 429/5xx
retries, and keeps one HTTPS connection alive per thread (saves the TLS
handshake on every request, ~25% per call).
"""
from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

API_HOST = "api.notion.com"
BASE_URL = f"https://{API_HOST}/v1"
NOTION_VERSION = "2025-09-03"
USER_AGENT = "notion-anki-sync/2.0"

MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 2


class NotionError(Exception):
    """Raised when Notion returns a permanent error."""


class NotionClient:
    def __init__(self, token: str):
        if not token:
            raise NotionError("No Notion API token configured")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        self._local = threading.local()

    # ------------------------------------------------------------------ #
    # Low level
    # ------------------------------------------------------------------ #

    def _connection(self) -> http.client.HTTPSConnection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = http.client.HTTPSConnection(API_HOST, timeout=60)
            self._local.conn = conn
        return conn

    def _drop_connection(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _request(self, method: str, path: str, data: Optional[dict] = None) -> dict:
        if path.startswith(BASE_URL):
            path = path[len(BASE_URL):]
        url = "/v1" + path
        body = json.dumps(data).encode("utf-8") if data is not None else None
        attempt = 0
        while True:
            attempt += 1
            try:
                conn = self._connection()
                conn.request(method, url, body=body, headers=self._headers)
                resp = conn.getresponse()
                payload = resp.read()
                status = resp.status
                retry_after = resp.getheader("Retry-After")
                if (resp.getheader("Connection") or "").lower() == "close":
                    self._drop_connection()
            except (http.client.HTTPException, OSError) as e:
                # stale keep-alive socket or network blip: reconnect and retry
                self._drop_connection()
                if attempt > MAX_RETRIES:
                    raise NotionError(f"Network error calling Notion for {path}: {e}") from e
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt - 1))
                continue
            if status < 400:
                return json.loads(payload.decode("utf-8"))
            if status == 429 or status >= 500:
                if attempt > MAX_RETRIES:
                    raise NotionError(f"Notion API error {status} for {path}: {payload[:300]!r}")
                delay = float(retry_after) if retry_after else RETRY_BACKOFF_SECONDS * attempt
                time.sleep(min(delay, 30.0))
                continue
            raise NotionError(f"Notion API error {status} for {path}: {payload[:300]!r}")

    def _iterate(self, results: List[dict], has_more: bool) -> List[dict]:
        return list(results)

    # ------------------------------------------------------------------ #
    # Read endpoints
    # ------------------------------------------------------------------ #

    def get_page(self, page_id: str) -> dict:
        return self._request("GET", f"/pages/{page_id}")

    def get_block_children(self, block_id: str) -> List[dict]:
        """All children blocks of a page or block, following pagination."""
        url = f"{BASE_URL}/blocks/{block_id}/children?page_size=100"
        out: List[dict] = []
        while True:
            data = self._request("GET", url)
            out += data.get("results", [])
            if not data.get("has_more"):
                return out
            cursor = data.get("next_cursor")
            assert isinstance(cursor, str)
            url = f"{BASE_URL}/blocks/{block_id}/children?page_size=100&start_cursor={cursor}"

    def get_database(self, database_id: str) -> dict:
        return self._request("GET", f"/databases/{database_id}")

    def get_data_source(self, data_source_id: str) -> dict:
        return self._request("GET", f"/data_sources/{data_source_id}")

    def query_data_source(self, data_source_id: str, title_only: bool = True) -> List[dict]:
        """All row pages of a data source, following pagination.

        ``title_only`` asks Notion to return just the title property: the
        sync only needs the title plus each row's built-in id and
        last_edited_time, so the other columns are dead weight.
        """
        out: List[dict] = []
        path = f"/data_sources/{data_source_id}/query"
        if title_only:
            path += "?filter_properties=title"
        data: dict = {"results": [], "has_more": True, "next_cursor": None}
        while data.get("has_more"):
            payload: dict = {"page_size": 100}
            if data.get("next_cursor"):
                payload["start_cursor"] = data["next_cursor"]
            data = self._request("POST", path, payload)
            out += data.get("results", [])
        return out

    def download_file(self, url: str) -> bytes:
        """Download a Notion-hosted (or external) file. No auth needed."""
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req) as resp:
            return resp.read()


def page_title(obj: dict) -> str:
    """Extract the plain-text title from a page object."""
    for prop in obj.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(x.get("plain_text", "") for x in prop.get("title", [])).strip()
    return ""


class BlockTree:
    """Lazy consumer of a client to resolve nested block children on demand."""

    def __init__(self, client: NotionClient):
        self.client = client

    def children(self, block_id: str) -> List[dict]:
        return self.client.get_block_children(block_id)


def data_source_of(database: dict) -> Dict[str, Any]:
    """Return the first data source of a database (title + id + schema)."""
    sources = database.get("data_sources") or []
    if not sources:
        return {}
    return sources[0]
