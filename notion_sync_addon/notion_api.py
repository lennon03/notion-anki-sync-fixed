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
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Protocol

API_HOST = "api.notion.com"
BASE_URL = f"https://{API_HOST}/v1"
NOTION_VERSION = "2025-09-03"
USER_AGENT = "notion-anki-sync/2.0"

MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 2
MAX_RETRY_DELAY_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 60


class NotionError(Exception):
    """Raised when a Notion request fails.

    ``status`` is the HTTP status (None for network errors). ``transient``
    is True for failures that say nothing about the object itself (network
    errors, rate limits and server errors that outlasted the retries): only
    a non-transient error means Notion rejected the request as such.
    """

    def __init__(
        self,
        message: str,
        status: Optional[int] = None,
        transient: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.transient = transient

    def reason(self) -> str:
        """Explain the failure in a few user-facing words."""
        if self.status in (401,):
            return "the token was rejected (401)"
        if self.status in (403, 404):
            return (
                f"not found or not shared with the integration ({self.status})"
            )
        if self.status == 429:
            return "rate limited by Notion (429)"
        if self.status is not None and self.status >= 500:
            return f"Notion server error ({self.status})"
        if self.status is not None:
            return f"Notion rejected the request ({self.status})"
        if self.transient:
            return "network error"
        return str(self)


class NotionClient:
    """Read-only Notion API client authenticated with ``token``."""

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
            conn = http.client.HTTPSConnection(
                API_HOST, timeout=REQUEST_TIMEOUT_SECONDS
            )
            self._local.conn = conn
        return conn

    def _drop_connection(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _request(
        self, method: str, path: str, data: Optional[dict] = None
    ) -> dict:
        if path.startswith(BASE_URL):
            path = path[len(BASE_URL) :]
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
                    raise NotionError(
                        f"Network error calling Notion for {path}: {e}",
                        transient=True,
                    ) from e
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt - 1))
                continue
            if status < 400:
                try:
                    return json.loads(payload.decode("utf-8"))
                except ValueError as e:  # truncated or non-JSON body
                    raise NotionError(
                        f"Invalid response from Notion for {path}: {e}",
                        status,
                        transient=True,
                    ) from e
            if status == 429 or status >= 500:
                if attempt > MAX_RETRIES:
                    raise NotionError(
                        _error_text(status, path, payload),
                        status,
                        transient=True,
                    )
                delay = retry_delay(
                    retry_after, RETRY_BACKOFF_SECONDS * attempt
                )
                time.sleep(min(delay, MAX_RETRY_DELAY_SECONDS))
                continue
            raise NotionError(
                _error_text(status, path, payload),
                status,
            )

    def _iterate(self, results: List[dict], has_more: bool) -> List[dict]:
        return list(results)

    # ------------------------------------------------------------------ #
    # Read endpoints
    # ------------------------------------------------------------------ #

    def get_page(self, page_id: str) -> dict:
        """GET /pages/<id>: a page, or a table row (rows are pages)."""
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
            url = (
                f"{BASE_URL}/blocks/{block_id}/children"
                f"?page_size=100&start_cursor={cursor}"
            )

    def get_database(self, database_id: str) -> dict:
        """GET /databases/<id>: title and data sources of a database."""
        return self._request("GET", f"/databases/{database_id}")

    def get_data_source(self, data_source_id: str) -> dict:
        """GET /data_sources/<id>."""
        return self._request("GET", f"/data_sources/{data_source_id}")

    def query_data_source(
        self, data_source_id: str, title_only: bool = False
    ) -> List[dict]:
        """All row pages of a data source, following pagination.

        Every property comes back by default: the sync reads its checkbox,
        tags and other columns from the listing (same number of requests).
        ``title_only`` asks Notion for just the title property instead.
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
        with urllib.request.urlopen(
            req, timeout=REQUEST_TIMEOUT_SECONDS
        ) as resp:
            return resp.read()


def _error_text(status: int, path: str, payload: bytes) -> str:
    return f"Notion API error {status} for {path}: {payload[:300]!r}"


def retry_delay(retry_after: Optional[str], default: float) -> float:
    """Seconds to wait from a Retry-After header: delta-seconds or an
    HTTP date; anything unreadable falls back to ``default``.
    """
    value = (retry_after or "").strip()
    if not value:
        return default
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return default
    if when is None:
        return default
    return max(0.0, when.timestamp() - time.time())


def page_title(obj: dict) -> str:
    """Extract the plain-text title from a page object."""
    for prop in obj.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(
                x.get("plain_text", "") for x in prop.get("title", [])
            ).strip()
    return ""


class BlockSource(Protocol):
    """Anything that lists a block's children (BlockTree, test fakes)."""

    def children(self, block_id: str) -> List[dict]:
        """Return all child blocks of ``block_id``."""
        ...


class BlockTree:
    """Lazy consumer of a client to resolve nested block children on demand."""

    def __init__(self, client: NotionClient):
        self.client = client

    def children(self, block_id: str) -> List[dict]:
        """Return all child blocks of ``block_id``."""
        return self.client.get_block_children(block_id)


def data_source_of(database: dict) -> Dict[str, Any]:
    """Return the first data source of a database (title + id + schema)."""
    sources = database.get("data_sources") or []
    if not sources:
        return {}
    return sources[0]
