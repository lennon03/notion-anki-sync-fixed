# -*- coding: utf-8 -*-
"""Phase 1 of a sync: read Notion (network only, no collection).

``fetch_pages`` walks the configured pages and their tables, renders each
row, decides its card kind and downloads the media it uses.
"""

from __future__ import annotations

import html
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from .cache import Cache, RowState, _fresh, is_unchanged
from .htmlify import (
    MediaCollector,
    StrikeTags,
    render_blocks,
    render_lines,
    rich_text_to_html,
)
from .media import (
    _download_media,
    _forget_rows_with_failed_media,
    _stage_media,
)
from .note_types import TAG_PREFIX
from .notion_api import (
    BlockSource,
    BlockTree,
    NotionClient,
    NotionError,
    bare_id,
    canonical_page_id,
    data_source_of,
    page_title,
    plain_text,
    title_rich_text,
)
from .pagecards import PageCard, has_cloze, parse_page, rt_text
from .rows import (
    OVERLAP_SYNTAX,
    PAGE_CARDS,
    OverlapSpec,
    PageData,
    Progress,
    RowCard,
    RowColumns,
    SyncResult,
    TableData,
    _fail,
    _origin,
    clean_tags,
    parse_overlap,
    row_columns,
)

# When part of Notion can't be read, notes whose row origin is unknown are
# looked up one by one (GET /pages/<row>); at most this many per sync.
MAX_ORIGIN_CHECKS = 25
# A row that was asked about is asked again after this long, so rows that
# keep failing don't starve the ones not yet asked.
ORIGIN_RECHECK_SECONDS = 24 * 3600
# Rows of a table are downloaded this many at a time (Notion allows about
# three requests a second; the client retries a 429 after its delay).
ROW_WORKERS = 3


def resolve_token(cfg: Optional[dict]) -> str:
    """Order: config value -> NOTION_API_TOKEN env var -> .env file."""
    if cfg and str(cfg.get("notion_token") or "").strip():
        return str(cfg["notion_token"]).strip()
    val = os.environ.get("NOTION_API_TOKEN", "").strip()
    if val:
        return val
    try:
        from aqt import mw  # noqa: F401

        addon_dir = os.path.dirname(os.path.abspath(__file__))
        bases: Tuple[str, ...] = (
            os.path.join(addon_dir, "user_files"),
            addon_dir,
            os.getcwd(),
            os.path.expanduser("~"),
        )
    except Exception:
        bases = (os.getcwd(), os.path.expanduser("~"))
    for base in bases:
        value = _dotenv_value(
            _read_dotenv(os.path.join(base, ".env")), "NOTION_API_TOKEN"
        )
        if value:
            return value
    return ""


def _read_dotenv(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except OSError:
        return ""


def _dotenv_value(content: str, key: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith(key + "="):
            val = line.split("=", 1)[1].strip().strip("'\"")
            if val and val != "your_token_here":
                return val
    return ""


def deck_safe(name: str) -> str:
    """Turn a Notion title into a valid Anki deck name component."""
    name = re.sub(r"\s+", " ", name).strip()
    name = name.replace("::", "/")  # protect path separator first
    name = re.sub(r"[\x00:\"]", " ", name)  # drop any other illegal chars
    return name or "Untitled"


def fetch_pages(
    cfg: dict,
    known: Optional[Dict[str, RowState]] = None,
    progress: Optional[Progress] = None,
    structure: Optional[Dict[str, dict]] = None,
    previous: Optional[Cache] = None,
    sources: Optional[Set[str]] = None,
    media_folder: str = "",
    should_stop: Optional[Callable[[], bool]] = None,
) -> SyncResult:
    """Read all configured pages. Rows in ``known`` that were not edited
    since they were fetched are returned as ``unchanged`` placeholders.
    ``structure`` (pages/dbs of the cache) skips re-reading unchanged
    pages and re-resolving data source ids. ``previous`` is the whole cache,
    used to scope note removal when part of Notion can't be read.
    ``sources`` are the Sources of the synced notes in the collection: when
    part of Notion can't be read, unlisted ones of unknown origin are
    looked up so they can be removed or kept for certain.
    New media files are copied into ``media_folder`` here, without the
    collection, until ``should_stop()`` says to give up.
    """
    client = NotionClient(resolve_token(cfg))
    tree = BlockTree(client)
    media = MediaCollector()
    result = SyncResult()
    result.config = cfg
    result.known = dict(known or {})
    result.previous = previous or Cache()
    result.sources = set(sources or ())
    if structure:
        result.page_cache = dict(structure.get("pages") or {})
        result.db_cache = dict(structure.get("dbs") or {})
    if progress is not None:
        result.progress = progress
    result.media_dir = tempfile.mkdtemp(prefix="notion-anki-sync-")
    try:
        for entry in cfg.get("notion_pages", []):
            raw = entry if isinstance(entry, str) else entry.get("page_id", "")
            raw = str(raw).strip()
            if not raw:
                continue
            try:
                page_id = canonical_page_id(raw)
            except NotionError:
                # a config mistake, not a Notion failure: no card ever came
                # from this entry, so it doesn't hold up removal elsewhere
                # (see _removal_blocked for an entry that used to be valid)
                result.invalid_entries.append(raw)
                message = (
                    f"Ignored page '{raw}' in the settings: "
                    "not a Notion page link or id"
                )
                result.warnings.append(message)
                result.problems.append(message)
                continue
            if page_id in result.page_ids:  # listed twice: read it once
                continue
            result.page_ids.append(page_id)
            try:
                _fetch_page(page_id, client, tree, media, result)
            except NotionError as e:
                _fail(
                    result,
                    str(e),
                    page_id=page_id,
                    problem=f"Page {page_id[:8]}...: {e.reason()}",
                )

        if result.sources and result.incomplete:
            _check_unlisted(client, result)
        result.media_files, result.failed_media = _download_media(
            client, media, result.media_dir, result.progress
        )
        _stage_media(result, media_folder, should_stop)
        _forget_rows_with_failed_media(result)
    except BaseException:
        result.cleanup()
        raise
    return result


def _fetch_page(
    page_id: str,
    client: NotionClient,
    tree: BlockSource,
    media: MediaCollector,
    result: SyncResult,
) -> None:
    result.progress("reading pages")
    page = client.get_page(page_id)
    title = page_title(page) or page_id[:8]
    data = PageData(title=title, deck_name=deck_safe(title))
    result.pages.append(data)
    failures = result.failures

    edited = str(page.get("last_edited_time") or "")
    cached = result.page_cache.get(page_id)
    if cached and _fresh(cached[0], float(cached[1]), edited, page_id, None):
        # page untouched: same tables as last time, skip walking its blocks
        for db_id, db_title in cached[2]:
            block = {
                "id": db_id,
                "type": "child_database",
                "child_database": {"title": db_title},
            }
            table = _fetch_table(
                client, tree, block, media, data.deck_name, result, page_id
            )
            if table is not None:
                data.tables.append(table)
        _page_cards(
            page_id,
            edited,
            None,
            client,
            tree,
            media,
            data,
            result,
            read_before=len(cached) > 3 and bool(cached[3]),
        )
        if result.failures == failures:
            result.pages_out[page_id] = cached
        return

    fetched_at = time.time()
    blocks = tree.children(page_id)
    _collect_page_children(page_id, client, tree, media, data, result, blocks)
    _page_cards(
        page_id,
        edited,
        blocks,
        client,
        tree,
        media,
        data,
        result,
        read_at=fetched_at,
    )
    # a page with any failure keeps its previous cache entry: a table that
    # could not be read must not vanish from the cached table list
    if edited and result.failures == failures:
        entry: list = [
            edited,
            fetched_at,
            [
                [t.db_id, t.raw_title]
                for t in data.tables
                if not t.db_id.startswith(PAGE_CARDS)
            ],
        ]
        if result.config.get("page_cards", True):
            entry.append(1)  # the page's own cards were read
        result.pages_out[page_id] = entry


def _numbered(blocks: List[dict]) -> bool:
    """Tell whether the blocks are a numbered list of two items or more."""
    items = [b for b in blocks if b.get("type") != "divider"]
    return len(items) >= 2 and all(
        b.get("type") == "numbered_list_item" for b in items
    )


def _page_card_type(card: PageCard, answer: List[dict]) -> str:
    """Return the "Card type" a found card is rendered as ("" = by
    content): overlapping when options are written after the question or
    the answer is a numbered list.
    """
    if card.spec is not None:
        rest = re.sub(r"(?i)^overlap(?:ping)?", "", card.spec).strip().lower()
        rest = re.sub(r"(\d)\s*x", r"\1 x", rest)
        return f"overlapping {rest}".strip()
    if card.origin in ("heading", "qa") and _numbered(split_hint(answer)[0]):
        return "overlapping"
    return ""


def _page_cards(
    page_id: str,
    edited: str,
    blocks: Optional[List[dict]],
    client: NotionClient,
    tree: BlockSource,
    media: MediaCollector,
    data: PageData,
    result: SyncResult,
    read_before: bool = False,
    read_at: float = 0.0,
) -> None:
    """Read the cards written as text in a page (see pagecards.py).

    ``blocks`` is None when the page itself is unchanged: its cards are
    then reused, unless one is missing from the cache or has to be checked
    again, in which case the page's blocks are read. ``read_at`` is when
    ``blocks`` were read: the cards are no newer than that.
    """
    if not result.config.get("page_cards", True):
        return
    marker = PAGE_CARDS + page_id
    result.db_pages[marker] = page_id
    table = TableData(title="", db_id=marker, raw_title="")
    url = "https://www.notion.so/" + page_id
    if blocks is None:
        now = time.time()
        listed = [
            rid
            for rid, state in result.previous.rows.items()
            if state.db_id == marker and state.page_id == page_id
        ]
        if listed and all(
            rid in result.known
            and is_unchanged(result.known[rid], edited, rid, now)
            for rid in listed
        ):
            for rid in listed:
                result.fetched[rid] = replace(result.known[rid])
                table.rows.append(
                    RowCard(rid, "", "", url=url, unchanged=True)
                )
            data.tables.append(table)
            return
        if not listed and read_before:
            return  # read before, and it held no card
        try:
            read_at = time.time()
            blocks = tree.children(page_id)
        except NotionError as e:
            _fail(
                result,
                f"Page {data.title}: {e}",
                page_id=page_id,
                problem=f"Page '{data.title}': {e.reason()}",
            )
            return
    seen: Set[str] = set()
    for card in parse_page(blocks):
        if card.source_id in seen:
            continue
        seen.add(card.source_id)
        recorder = _RecordingTree(tree)
        strike = StrikeTags(recorder)
        try:
            row = _render_page_card(card, url, media, strike, result)
        except NotionError as e:
            result.protected.add(card.source_id)
            _fail(
                result,
                f"Card in '{data.title}': {e}",
                page_id=page_id,
                problem=f"Card in '{data.title}': {e.reason()}",
            )
            continue
        if row is None:
            continue
        row.tags = [TAG_PREFIX + t for t in clean_tags(strike.tags)]
        # the card's own blocks came with the page listing, not through
        # the recorder: check them for mentions and synced copies here
        volatile = (
            recorder.volatile
            or _has_live_mention(card.title)
            or any(_is_volatile(b) for b in card.blocks)
        )
        result.fetched[card.source_id] = RowState(
            edited,
            read_at or time.time(),
            "",
            page_id,
            marker,
            1 if volatile else 0,
        )
        table.rows.append(row)
    if table.rows:
        data.tables.append(table)


def _render_page_card(
    card: PageCard,
    url: str,
    media: MediaCollector,
    tree: StrikeTags,
    result: SyncResult,
) -> Optional[RowCard]:
    """Render a found card; None if nothing is left of it."""
    title_rts = tree.strip_rich(card.title)
    title = rt_text(title_rts).strip()
    front = title_html(title_rts, title)
    if card.origin == "cloze":
        if not has_cloze(title):
            return None
        return RowCard(
            card.source_id, front, "", url=url, kind="cloze", title=title
        )
    if not title:
        return None
    answer = tree.strip_blocks(card.blocks)
    done = _render_blocks(
        answer,
        title,
        _page_card_type(card, answer),
        media,
        tree,
        result.warnings,
    )
    return RowCard(
        card.source_id,
        front,
        done.back,
        url=url,
        context_html=done.hint,
        kind=done.kind,
        lines=done.lines,
        overlap=done.overlap,
        title=title,
    )


def _collect_page_children(
    page_id: str,
    client: NotionClient,
    tree: BlockSource,
    media: MediaCollector,
    data: PageData,
    result: SyncResult,
    blocks: List[dict],
) -> None:
    databases = []
    for block in blocks:
        if block.get("type") == "child_database":
            databases.append(block)
        elif block.get("type") == "column_list" and block.get("has_children"):
            for col in tree.children(block["id"]):
                if col.get("type") != "column" or not col.get("has_children"):
                    continue
                databases += [
                    inner
                    for inner in tree.children(col["id"])
                    if inner.get("type") == "child_database"
                ]
    for block in databases:
        table = _fetch_table(
            client, tree, block, media, data.deck_name, result, page_id
        )
        if table is not None:
            data.tables.append(table)


class _RecordingTree:
    """Passes children() through and notes content that can change in
    Notion without bumping the row's last_edited_time.
    """

    def __init__(self, tree: Any) -> None:
        self.tree = tree
        self.volatile = False

    def children(self, block_id: str) -> List[dict]:
        blocks = self.tree.children(block_id)
        if not self.volatile:
            self.volatile = any(_is_volatile(b) for b in blocks)
        return blocks


# mentions whose text is another object's current name
_VOLATILE_MENTIONS = ("page", "database", "user")


def _has_live_mention(rich_text: Any) -> bool:
    if not isinstance(rich_text, list):
        return False
    for rt in rich_text:
        if isinstance(rt, dict) and rt.get("type") == "mention":
            if (rt.get("mention") or {}).get("type") in _VOLATILE_MENTIONS:
                return True
    return False


def _is_volatile(block: dict) -> bool:
    btype = block.get("type") or ""
    body = block.get(btype) or {}
    if not isinstance(body, dict):
        return False
    # a copy of a synced block: the original lives (and is edited) elsewhere
    if btype == "synced_block" and body.get("synced_from"):
        return True
    # a page mention shows the other page's current title; rich text sits
    # in the block's text, its caption (media, bookmarks) and table cells
    if _has_live_mention(body.get("rich_text")) or _has_live_mention(
        body.get("caption")
    ):
        return True
    return any(_has_live_mention(cell) for cell in body.get("cells") or [])


def _resolve_table(
    client: NotionClient, db_id: str, block_title: str
) -> Tuple[str, str]:
    """Look up a database's data source ids and title.

    A database can hold several data sources: their ids are joined with
    commas, so a database with one source is cached as before.
    """
    db = client.get_database(db_id)
    ds = data_source_of(db) or {}
    title = (
        plain_text(db.get("title")).strip()
        or ds.get("name", "").strip()
        or block_title
        or "Table"
    )
    ids = [
        str(s["id"])
        for s in db.get("data_sources") or []
        if isinstance(s, dict) and s.get("id")
    ]
    return ",".join(ids) or db_id, title


def _query_rows(client: NotionClient, ds_ids: str) -> List[dict]:
    """List the rows of every data source of a table, in order."""
    rows: List[dict] = []
    for ds_id in ds_ids.split(","):
        rows += client.query_data_source(ds_id)
    return rows


def _fetch_table(
    client: NotionClient,
    tree: BlockSource,
    db_block: dict,
    media: MediaCollector,
    parent_deck: str,
    result: SyncResult,
    page_id: str = "",
) -> Optional[TableData]:
    db_id = db_block.get("id", "")
    block_title = str(
        (db_block.get("child_database") or {}).get("title") or ""
    ).strip()
    if page_id:
        result.db_pages[db_id] = page_id
    cached_ds = result.db_cache.get(db_id, "")
    where = f" on '{parent_deck}'" if parent_deck else ""
    if cached_ds and block_title:
        # the page block already has the title: no database lookup needed
        ds_id, title = cached_ds, block_title
    else:
        cached_ds = ""
        try:
            ds_id, title = _resolve_table(client, db_id, block_title)
        except NotionError as e:
            _fail(
                result,
                f"Table {block_title or db_id}: {e}",
                page_id,
                db_id,
                f"Table '{block_title or db_id}'{where}: {e.reason()}",
            )
            return None
    table = TableData(title=deck_safe(title), db_id=db_id, raw_title=title)
    result.progress(title)
    try:
        # one cheap metadata query per 100 rows; every row carries its
        # built-in last_edited_time, no table property is needed
        rows = _query_rows(client, ds_id)
    except NotionError as e:
        error = e
        retried: Optional[List[dict]] = None
        if cached_ds and not getattr(e, "transient", False):
            # Notion rejected the remembered data source (deleted or
            # replaced): look the database up again, once
            result.dbs_drop.add(db_id)
            try:
                fresh_ds, title = _resolve_table(client, db_id, block_title)
                table.title, table.raw_title = deck_safe(title), title
                if fresh_ds != ds_id:
                    ds_id = fresh_ds
                    retried = _query_rows(client, ds_id)
            except NotionError as again:
                error = again
        if retried is None:
            _fail(
                result,
                f"Table '{title}': {error}",
                page_id,
                db_id,
                f"Table '{title}'{where}: {error.reason()}",
            )
            return table
        rows = retried
    result.dbs_out[db_id] = ds_id

    now = time.time()
    # rows to download wait in ``slots`` so the cards keep the table's order
    slots: List[Union[RowCard, _RowJob]] = []
    for index, row in enumerate(rows, 1):
        title_rich = title_rich_text(row)
        front = plain_text(title_rich).strip()
        if not front and not bool(
            result.config.get("include_empty_cards", True)
        ):
            result.warnings.append(
                f"Suspended empty row in table '{title}' (no title): skipped"
            )
            continue
        row_id = row["id"].replace("-", "")
        edited = str(row.get("last_edited_time") or "")
        columns = row_columns(row, result.config)
        if columns.sync is False:
            # unchecked: no card and no download. Only where the row came
            # from is kept; its content is read when it is checked again
            result.fetched[row_id] = RowState("", 0.0, "", page_id, db_id)
            slots.append(
                RowCard(
                    source_id=row_id,
                    front=front,
                    back_html="",
                    url=row.get("url", ""),
                    unchecked=True,
                    columns=columns,
                    title=front,
                )
            )
            continue
        cached = result.known.get(row_id)
        if is_unchanged(cached, edited, row_id, now):
            assert cached is not None
            result.fetched[row_id] = replace(
                cached, page_id=page_id, db_id=db_id
            )
            slots.append(
                RowCard(
                    source_id=row_id,
                    front=front,
                    back_html="",
                    url=row.get("url", ""),
                    unchanged=True,
                    columns=columns,
                    title=front,
                )
            )
            continue
        slots.append(_RowJob(index, row, front, title_rich, columns))

    jobs = [slot for slot in slots if isinstance(slot, _RowJob)]
    pool = ThreadPoolExecutor(max_workers=ROW_WORKERS)
    try:
        # downloaded side by side, used here one by one in listing order
        downloads = pool.map(lambda job: _download_row(job, tree), jobs)
        for slot in slots:
            if isinstance(slot, RowCard):
                table.rows.append(slot)
                continue
            job = slot
            result.progress(f"{title} {job.index}/{len(rows)}")
            got = next(downloads)
            row_id = job.row["id"].replace("-", "")
            if got.error is not None:
                # still listed in Notion: keep its note, retry next sync
                result.protected.add(row_id)
                _fail(
                    result,
                    f"Row '{job.title}' in '{title}': {got.error}",
                    page_id,
                    db_id,
                    f"Row '{job.title}' in '{title}': "
                    f"{got.error.reason()}",
                )
                continue
            assert got.done is not None
            media.extend(got.media)
            result.warnings += got.warnings
            result.fetched[row_id] = RowState(
                str(job.row.get("last_edited_time") or ""),
                got.fetched_at,
                "",
                page_id,
                db_id,
                1 if got.volatile else 0,
            )
            table.rows.append(
                RowCard(
                    source_id=row_id,
                    front=title_html(job.title_rich, job.title),
                    back_html=got.done.back,
                    context_html=got.done.hint,
                    url=job.row.get("url", ""),
                    tags=got.tags,
                    columns=job.columns,
                    kind=got.done.kind,
                    lines=got.done.lines,
                    overlap=got.done.overlap,
                    title=job.title,
                )
            )
    finally:
        pool.shutdown(cancel_futures=True)
    return table


HINT_HEADING = "hint"


def split_hint(blocks: List[dict]) -> Tuple[List[dict], List[dict]]:
    """Split a page into (card blocks, hint blocks).

    A ``#### Hint`` heading starts the hint; it runs up to the next divider
    (``---``), which is dropped, or to the end of the page. What comes
    before the heading and after the divider stays on the card.
    """
    for start, block in enumerate(blocks):
        if block.get("type") != "heading_4":
            continue
        text = plain_text((block.get("heading_4") or {}).get("rich_text"))
        if text.strip().lower() != HINT_HEADING:
            continue
        rest = blocks[start + 1 :]
        end = next(
            (i for i, b in enumerate(rest) if b.get("type") == "divider"),
            None,
        )
        if end is None:
            return blocks[:start], rest
        return blocks[:start] + rest[end + 1 :], rest[:end]
    return blocks, []


@dataclass
class _RowJob:
    """A listed row whose page has to be downloaded."""

    index: int  # position in the table listing, from 1
    row: dict  # the row as the listing gave it
    title: str  # plain title
    title_rich: List[dict]
    columns: RowColumns


@dataclass
class _Downloaded:
    """What downloading one row gave: a card, or the error."""

    done: Optional["Rendered"] = None
    error: Optional[NotionError] = None
    media: MediaCollector = field(default_factory=MediaCollector)
    warnings: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    volatile: bool = False
    fetched_at: float = 0.0


def _download_row(job: _RowJob, tree: BlockSource) -> _Downloaded:
    """Download and render one row; runs in a worker thread.

    Everything it finds goes into its own ``_Downloaded``: the caller adds
    it to the sync's result on its own thread, in listing order.
    """
    got = _Downloaded(fetched_at=time.time())
    recorder = _RecordingTree(tree)
    struck = StrikeTags(recorder)
    try:
        got.done = _render_row(
            job.row["id"],
            job.title,
            job.columns.card_type,
            got.media,
            struck,
            got.warnings,
        )
    except NotionError as e:
        got.error = e
        return got
    got.tags = [TAG_PREFIX + t for t in clean_tags(struck.tags)]
    got.volatile = recorder.volatile or _has_live_mention(job.title_rich)
    return got


def title_html(rich: List[dict], plain: str) -> str:
    """Render a row's or page card's title as its Front field.

    Bold, inline code and inline equations are kept. A cloze marker that
    formatting cuts in two would no longer be a cloze: such a title is
    written as plain text instead.
    """
    out = rich_text_to_html(rich).strip()
    if has_cloze(plain) and not has_cloze(out):
        return html.escape(plain, quote=False)
    return out


@dataclass
class Rendered:
    """A rendered row or page card, before it becomes a ``RowCard``."""

    kind: str  # "basic", "cloze" or "overlapping"
    back: str  # page HTML (the joined lines for an overlapping cloze)
    hint: str  # the collapsed Hint section, or ""
    lines: List[str] = field(default_factory=list)  # overlapping lines
    overlap: Optional[OverlapSpec] = None  # overlapping options


def _render_row(
    row_id: str,
    title: str,
    card_type: str,
    media: MediaCollector,
    tree: Any,
    warnings: List[str],
) -> Rendered:
    """Download and render a row.

    The page is read once; the "Card type" column decides the kind,
    otherwise a cloze marker in the title or page makes it a cloze.
    """
    return _render_blocks(
        tree.children(row_id), title, card_type, media, tree, warnings
    )


def _render_blocks(
    all_blocks: List[dict],
    title: str,
    card_type: str,
    media: MediaCollector,
    tree: Any,
    warnings: List[str],
) -> Rendered:
    """Render the blocks of a row or page card (see ``_render_row``)."""
    blocks, hint_blocks = split_hint(all_blocks)
    hint = ""
    if hint_blocks:
        body = render_blocks(hint_blocks, media, tree)
        if body:
            hint = (
                '<details class="ns-context"><summary>Hint</summary>'
                f"{body}</details>"
            )
    if card_type.split(None, 1)[:1] == ["overlapping"]:
        spec = parse_overlap(card_type)
        if spec is None:
            warnings.append(
                f"Row '{title}': Card type '{card_type}' is not understood "
                f"(use {OVERLAP_SYNTAX}); used plain Overlapping"
            )
            spec = OverlapSpec()
        lines = render_lines(blocks, media, tree)
        if lines:
            options = None if spec == OverlapSpec() else spec
            return Rendered(
                "overlapping", "\n".join(lines), hint, lines, options
            )
        warnings.append(
            f"Row '{title}': Card type is Overlapping but the page has no "
            "line; made a basic card"
        )
        return Rendered("basic", "", hint)
    back = render_blocks(blocks, media, tree)
    if card_type == "basic":
        return Rendered("basic", back, hint)
    if has_cloze(title) or has_cloze(back):
        return Rendered("cloze", back, hint)
    if card_type == "cloze":
        warnings.append(
            f"Row '{title}': Card type is Cloze but no {{{{c1::...}}}} "
            "was found; made a basic card"
        )
    return Rendered("basic", back, hint)


def _check_unlisted(client: NotionClient, result: SyncResult) -> None:
    """Look up synced notes that no table listed and whose origin is unknown.

    Only runs when part of Notion could not be read: such a note may belong
    to the part that failed, so it would be kept. Notion answers for the
    row itself: a row in the trash is gone for certain (its note can go);
    a row that exists keeps its note
    and, when its table belongs to a configured page, the origin is
    remembered so the next sync doesn't need to ask again. Any error (the
    row may just not be shared) keeps the note.
    """
    listed = {
        row.source_id
        for page in result.pages
        for table in page.tables
        for row in table.rows
    } | result.protected
    pending = sorted(
        s for s in result.sources - listed if _origin(s, result) is None
    )
    if not pending:
        return
    # database id -> (configured page, database id as the sync records it)
    tables: Dict[str, Tuple[str, str]] = {}
    for page_id, entry in result.previous.pages.items():
        if page_id in result.page_ids:
            for db_id, _title in entry[2]:
                tables[bare_id(db_id)] = (page_id, db_id)
    for db_id, page_id in result.db_pages.items():
        tables[bare_id(db_id)] = (page_id, db_id)
    now = time.time()
    asked = {**result.previous.checked, **result.checked}
    # never asked first, then the longest ago; rows asked within the last
    # day wait, so the same few failing rows can't use up every sync
    due = sorted(
        (
            s
            for s in pending
            if now - asked.get(s, 0.0) >= ORIGIN_RECHECK_SECONDS
        ),
        key=lambda s: (asked.get(s, 0.0), s),
    )
    batch = due[:MAX_ORIGIN_CHECKS]
    for source_id in batch:
        result.progress("checking unlisted rows")
        result.checked[source_id] = now  # whatever the answer: asked today
        try:
            row = client.get_page(source_id)
        except NotionError:
            continue  # not shared, or no answer: keep the note
        if row.get("in_trash") or row.get("archived"):
            result.confirmed_gone.add(source_id)
            continue
        result.protected.add(source_id)  # it exists: keep its note
        parent = row.get("parent") or {}
        known = tables.get(bare_id(parent.get("database_id") or ""))
        if known is not None:
            result.recovered[source_id] = RowState(
                "", 0.0, "", known[0], known[1]
            )
    if len(pending) > len(batch):
        result.warnings.append(
            f"{len(pending) - len(batch)} card(s) of unknown origin "
            "were kept without being checked this time; "
            "they are checked on the next syncs"
        )
