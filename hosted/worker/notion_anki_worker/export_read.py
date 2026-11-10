"""Read-only Notion lookups for the table exporter.

The site renders PDF, Word and HTML, but never holds the Notion token: it asks
the worker for the plain Notion content and gets it back as JSON. Everything
here only reads.
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from notion_sync_addon.notion_api import (
    NotionClient,
    NotionError,
    data_source_of,
    page_title,
    plain_text,
)

# Notion allows about three requests a second; the client retries on 429, so
# this only keeps one export from queueing a flood of calls.
WORKERS = 6
NOTION_SLOTS = threading.BoundedSemaphore(8)
# Lookups that would take longer than a request can wait are refused, not cut.
MAX_SCAN_REQUESTS = 400
MAX_TREE_BLOCKS = 5000
MAX_ROWS = 1000


class TooLarge(Exception):
    """The page or row has more content than one request may read."""


_ID = re.compile(
    r"[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE
)


def valid_id(raw: str) -> str:
    """Return the plain 32-hex id; raises ``NotionError`` if it isn't one."""
    value = raw.strip()
    if not _ID.fullmatch(value):
        raise NotionError("Not a Notion id")
    return value.replace("-", "").lower()


def _children(client: NotionClient, block_id: str) -> List[dict]:
    with NOTION_SLOTS:
        return client.get_block_children(block_id)


def _table_of(client: NotionClient, block: dict) -> Optional[Dict[str, str]]:
    with NOTION_SLOTS:
        database = client.get_database(block["id"])
    source = data_source_of(database)
    if not source.get("id"):
        return None
    title = (block.get("child_database") or {}).get("title")
    return {
        "id": source["id"],
        "name": title or source.get("name") or "Untitled table",
    }


def _tables_in(client: NotionClient, page_id: str) -> List[Dict[str, str]]:
    """Find the inline tables on a page, at any depth, in reading order."""
    found: List[Dict[str, str]] = []
    seen = set()
    requests = 0
    level: List[str] = [page_id]
    with ThreadPoolExecutor(WORKERS) as pool:
        while level:
            requests += len(level)
            if requests > MAX_SCAN_REQUESTS:
                raise TooLarge()
            listing = list(pool.map(lambda i: _children(client, i), level))
            level = []
            databases: List[dict] = []
            for blocks in listing:
                for block in blocks:
                    if block.get("type") == "child_database":
                        if block["id"] not in seen:
                            seen.add(block["id"])
                            databases.append(block)
                    if block.get("has_children"):
                        level.append(block["id"])
            requests += len(databases)
            found.extend(
                t
                for t in pool.map(lambda b: _table_of(client, b), databases)
                if t
            )
    return found


def describe(client: NotionClient, raw_id: str) -> Dict[str, Any]:
    """Say what an id points at: a page with its tables, or one table."""
    ident = valid_id(raw_id)
    try:
        page = client.get_page(ident)
    except NotionError as first:
        if first.transient or first.status in (401, 429):
            raise
        # not a page: the id may be a database or a data source
        try:
            database = client.get_database(ident)
            source = data_source_of(database)
            if source.get("id"):
                title = plain_text(database.get("title"))
                name = title or "Notion table"
                return {
                    "id": ident,
                    "name": name,
                    "tables": [{"id": source["id"], "name": name}],
                }
        except NotionError:
            pass
        try:
            client.query_data_source(ident, title_only=True)
        except NotionError:
            raise first from None
        return {
            "id": ident,
            "name": "Notion table",
            "tables": [{"id": ident, "name": "Notion table"}],
        }
    return {
        "id": ident,
        "name": page_title(page) or "Untitled",
        "tables": _tables_in(client, page["id"].replace("-", "")),
    }


def table_rows(client: NotionClient, raw_id: str) -> List[Dict[str, str]]:
    """List the rows of a table (data source): id and title, in order."""
    rows = client.query_data_source(valid_id(raw_id), title_only=True)
    if len(rows) > MAX_ROWS:
        raise TooLarge()
    return [
        {
            "id": row["id"].replace("-", ""),
            "title": page_title(row) or "Untitled",
        }
        for row in rows
    ]


def block_tree(client: NotionClient, raw_id: str) -> List[dict]:
    """Read a page's blocks, children nested under ``children``."""
    root = valid_id(raw_id)
    top = _children(client, root)
    total = len(top)
    parents = [b for b in top if b.get("has_children")]
    with ThreadPoolExecutor(WORKERS) as pool:
        while parents:
            listing = list(
                pool.map(lambda b: _children(client, b["id"]), parents)
            )
            parents_next: List[dict] = []
            for block, kids in zip(parents, listing):
                block["children"] = kids
                total += len(kids)
                parents_next.extend(k for k in kids if k.get("has_children"))
            if total > MAX_TREE_BLOCKS:
                raise TooLarge()
            parents = parents_next
    return top
