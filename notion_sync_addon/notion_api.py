# -*- coding: utf-8 -*-
"""Official Notion REST API client (Notion-Version 2025-09-03).

Only read endpoints are used: pages, block children, databases,
data sources and data source queries. Handles pagination and 429/5xx
retries.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

BASE_URL = "https://api.notion.com/v1"
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

    # ------------------------------------------------------------------ #
    # Low level
    # ------------------------------------------------------------------ #

    def _request(self, method: str, path: str, data: Optional[dict] = None) -> dict:
        url = path if path.startswith("http") else BASE_URL + path
        body = json.dumps(data).encode("utf-8") if data is not None else None
        attempt = 0
        while True:
            attempt += 1
            req = urllib.request.Request(url, data=body, headers=self._headers, method=method)
            try:
                with urllib.request.urlopen(req) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                status = e.code
                if status == 429 or status >= 500:
                    if attempt > MAX_RETRIES:
                        raise NotionError(f"Notion API error {status} for {path}: {e.read()[:300]}") from e
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    delay = float(retry_after) if retry_after else RETRY_BACKOFF_SECONDS * attempt
                    time.sleep(min(delay, 30.0))
                    continue
                raise NotionError(f"Notion API error {status} for {path}: {e.read()[:300]}") from e
            except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
                if attempt > MAX_RETRIES:
                    raise NotionError(f"Network error calling Notion for {path}: {e}") from e
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

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

    def query_data_source(self, data_source_id: str) -> List[dict]:
        """All row pages of a data source, following pagination."""
        out: List[dict] = []
        data: dict = {"results": [], "has_more": True, "next_cursor": None}
        while data.get("has_more"):
            payload: dict = {"page_size": 100}
            if data.get("next_cursor"):
                payload["start_cursor"] = data["next_cursor"]
            data = self._request("POST", f"/data_sources/{data_source_id}/query", payload)
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
