# -*- coding: utf-8 -*-
"""What a sync carries from Notion to the collection.

Pages, tables and rows as read from Notion, the ``SyncResult`` that holds
them, and how a row's columns and "Card type" options are read.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from .cache import Cache, RowState
from .notion_api import plain_text

Progress = Callable[[str], None]


def _no_progress(_message: str) -> None:
    pass


@dataclass
class RowCard:
    """One Notion row, to become one note."""

    source_id: str
    front: str
    back_html: str  # may contain @@NSMEDIA:*@@ placeholders
    url: str
    unchanged: bool = False  # content not fetched: note is already current
    tags: List[str] = field(default_factory=list)  # Notion::... tags
    unchecked: bool = False  # sync checkbox off: no card, content not read
    columns: Optional["RowColumns"] = None  # the row's parsed properties
    context_html: str = ""  # the page's "Hint" section, same placeholders
    # "basic", "cloze" or "overlapping" ("" = content not read)
    kind: str = ""
    lines: List[str] = field(default_factory=list)  # overlapping: its lines
    overlap: Optional["OverlapSpec"] = None  # overlapping: Card type options


@dataclass
class TableData:
    """One Notion table (a subdeck) and its rows."""

    title: str
    rows: List[RowCard] = field(default_factory=list)
    db_id: str = ""
    raw_title: str = ""  # Notion title before deck_safe()


@dataclass
class PageData:
    """One configured Notion page (a deck) and its tables."""

    title: str
    deck_name: str
    tables: List[TableData] = field(default_factory=list)


@dataclass
class Prepared:
    """Output of ``prepare_sync``: the cache checked against the notes."""

    known: Dict[str, RowState]
    structure: Dict[str, dict]
    previous: Cache
    sources: Set[str] = field(default_factory=set)  # Sources of synced notes
    media_folder: str = ""  # the collection's media folder ("" if unknown)


class SyncResult:
    """Everything read from Notion, plus what apply_sync needs to know."""

    def __init__(self):
        self.pages: List[PageData] = []
        self.config: dict = {}
        self.media_dir: str = ""
        self.media_files: List[Tuple[str, str]] = []  # (abs path, fname)
        self.failed_media: Set[str] = set()
        self.warnings: List[str] = []
        # trusted row cache before this run / cache entries after it
        self.known: Dict[str, RowState] = {}
        self.fetched: Dict[str, RowState] = {}
        # the whole cache file before this run (row origins, structure)
        self.previous: Cache = Cache()
        self.cache_path: str = ""
        # page structure and data source ids: before this run / after it
        self.page_cache: Dict[str, list] = {}
        self.db_cache: Dict[str, str] = {}
        self.pages_out: Dict[str, list] = {}
        self.dbs_out: Dict[str, str] = {}
        # table -> deck name, for the tables read cleanly (see _move_renamed)
        self.decks_out: Dict[str, str] = {}
        self.page_ids: List[str] = []  # configured pages, canonical ids
        # set when part of Notion could not be read. Notes are then only
        # removed if the page and table they last came from were read fine,
        # so a network error can never delete cards.
        self.incomplete = False
        self.failures = 0
        self.failed_pages: Set[str] = set()
        self.failed_dbs: Set[str] = set()
        self.unscoped_failure = (
            False  # a failure no page/table can be blamed for
        )
        self.protected: Set[str] = (
            set()
        )  # rows that exist in Notion but weren't read
        # config entries that aren't a Notion page id or link
        self.invalid_entries: List[str] = []
        # short descriptions of what could not be read (kept in the cache
        # file so the settings dialog can show them)
        self.problems: List[str] = []
        # unlisted rows Notion confirmed are in the trash: safe to remove
        self.confirmed_gone: Set[str] = set()
        # row origins learned by looking unlisted rows up (see _check_unlisted)
        self.recovered: Dict[str, RowState] = {}
        # unlisted rows asked about in this run: row id -> time
        self.checked: Dict[str, float] = {}
        # Sources of the synced notes before / after this sync
        self.sources: Set[str] = set()
        self.sources_after: Optional[Set[str]] = None
        self.dbs_drop: Set[str] = (
            set()
        )  # cached data source ids that stopped working
        self.db_pages: Dict[str, str] = (
            {}
        )  # every table met -> its configured page
        self.changes: Any = (
            None  # OpChanges of the undo step, None = no changes
        )
        self.progress: Progress = _no_progress

    def cleanup(self) -> None:
        """Delete the downloaded media files."""
        if self.media_dir:
            shutil.rmtree(self.media_dir, ignore_errors=True)


def _fail(
    result: SyncResult,
    message: str,
    page_id: str = "",
    db_id: str = "",
    problem: str = "",
) -> None:
    result.incomplete = True
    result.failures += 1
    result.warnings.append(message)
    result.problems.append(problem or message)
    if db_id:
        result.failed_dbs.add(db_id)
    elif page_id:
        result.failed_pages.add(page_id)
    else:
        result.unscoped_failure = True


COLUMN_DEFAULTS = {
    "sync_column": "Anki",
    "card_type_column": "Card type",
}


@dataclass
class RowColumns:
    """What a row's properties say, read by ``row_columns``."""

    sync: Optional[bool] = None  # None = the table has no sync column
    card_type: str = ""  # lowercased select name
    props: Dict[str, str] = field(default_factory=dict)  # name -> text


def _date_text(date: Any) -> str:
    if not isinstance(date, dict) or not date.get("start"):
        return ""
    text = str(date["start"])
    if date.get("end"):
        text += f" \u2192 {date['end']}"
    return text


def _names(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    return [
        str(i.get("name") or "")
        for i in items
        if isinstance(i, dict) and i.get("name")
    ]


def _value(prop: dict) -> Any:
    kind = prop.get("type")
    return prop.get(kind) if isinstance(kind, str) else None


def _prop_text(prop: dict) -> str:
    """Plain text of one property value (see ``row_columns``)."""
    kind = prop.get("type")
    value = _value(prop)
    if kind in ("title", "rich_text"):
        return plain_text(value)
    if kind in ("select", "status"):
        return str((value or {}).get("name") or "")
    if kind in ("multi_select", "people"):
        return ", ".join(_names(value))
    if kind == "number":
        return "" if value is None else str(value)
    if kind == "checkbox":
        return "Yes" if value else ""
    if kind == "date":
        return _date_text(value)
    if kind in ("url", "email", "phone_number"):
        return str(value or "")
    if kind == "formula" and isinstance(value, dict):
        inner = value.get(value.get("type") or "")
        if value.get("type") == "date":
            return _date_text(inner)
        if value.get("type") == "boolean":
            return "Yes" if inner else ""
        return "" if inner is None else str(inner)
    return ""


def _find_prop(props: dict, name: str) -> Optional[dict]:
    """Find the property called ``name`` (ignoring case and space)."""
    wanted = name.strip().lower()
    if not wanted:
        return None
    for key, prop in props.items():
        if str(key).strip().lower() == wanted and isinstance(prop, dict):
            return prop
    return None


def clean_tag(raw: str) -> str:
    """Make a Notion value a valid Anki tag ("" if nothing is left)."""
    text = re.sub(r"\s+", "_", raw.strip())
    text = re.sub(r'["\x00-\x1f\x7f]', "", text)
    parts = [p.strip(":") for p in text.split("::")]
    return "::".join(p for p in parts if p)


def clean_tags(values: Iterable[str]) -> List[str]:
    """Clean tags, dropping empties and case-insensitive duplicates."""
    out: Dict[str, str] = {}
    for value in values:
        tag = clean_tag(value)
        if tag:
            out.setdefault(tag.lower(), tag)
    return list(out.values())


def row_columns(row: dict, cfg: dict) -> RowColumns:
    """Read the special columns of a row listed by a data source query.

    Column names come from the config (``sync_column`` ...), matched
    ignoring case and surrounding space; an empty name disables the column.
    """

    def name(key: str) -> str:
        value = cfg.get(key, COLUMN_DEFAULTS[key])
        return "" if value is None else str(value)

    props = row.get("properties")
    props = props if isinstance(props, dict) else {}
    out = RowColumns()
    out.props = {
        str(key): _prop_text(prop)
        for key, prop in props.items()
        if isinstance(prop, dict)
    }
    sync = _find_prop(props, name("sync_column"))
    if sync is not None:
        if sync.get("type") == "checkbox":
            out.sync = bool(sync.get("checkbox"))
        elif sync.get("type") == "formula":
            formula = sync.get("formula") or {}
            if formula.get("type") == "boolean":
                out.sync = bool(formula.get("boolean"))
    card_type = _find_prop(props, name("card_type_column"))
    if card_type is not None and card_type.get("type") in (
        "select",
        "status",
    ):
        out.card_type = _prop_text(card_type).strip().lower()
    return out


PAGE_CARDS = "pagecards:"  # db_id prefix of a page's own cards


@dataclass(frozen=True)
class OverlapSpec:
    """Options of an ``Overlapping`` Card type, e.g. ``Overlapping 2/1 x2``.

    ``before``/``after`` are how many previous/following clozes are shown as
    cues (``before`` None = every previous one, none after: the plain
    behaviour); ``group`` is how many lines make one cloze.
    """

    before: Optional[int] = None
    after: int = 0
    group: int = 1

    @property
    def window(self) -> bool:
        """Tell whether only a window of lines is shown."""
        return self.before is not None


OVERLAP_MAX = 9  # largest before/after count
OVERLAP_MAX_GROUP = 20  # largest lines per cloze
OVERLAP_SYNTAX = (
    "Overlapping, Overlapping 2/1, Overlapping x2 or Overlapping 2/1 x2"
)
_OVERLAP_SPEC = re.compile(
    r"overlapping(?:\s+(\d+)\s*/\s*(\d+))?(?:\s+x\s*(\d+))?"
)


def parse_overlap(card_type: str) -> Optional[OverlapSpec]:
    """Parse a lowercased Card type that starts with ``overlapping``.

    ``overlapping``: the plain spec. ``overlapping B/A``: B previous and A
    following clozes shown. ``xN`` (after B/A or alone): N lines per cloze.
    None if the options are invalid or out of range.
    """
    m = _OVERLAP_SPEC.fullmatch(card_type.strip())
    if m is None:
        return None
    before = int(m.group(1)) if m.group(1) is not None else None
    after = int(m.group(2)) if m.group(2) is not None else 0
    group = int(m.group(3)) if m.group(3) is not None else 1
    if (before or 0) > OVERLAP_MAX or after > OVERLAP_MAX:
        return None
    if not 1 <= group <= OVERLAP_MAX_GROUP:
        return None
    return OverlapSpec(before, after, group)


def _origin(source_id: str, result: SyncResult) -> Optional[RowState]:
    """Where a note's row was last seen, if known."""
    origin = result.previous.rows.get(source_id) or result.recovered.get(
        source_id
    )
    if origin is None or not origin.page_id:
        return None
    return origin


def _forget(result: SyncResult, source_id: str) -> None:
    """Keep only where the row came from: its content is fetched next sync."""
    state = result.fetched.get(source_id)
    if state is not None:
        result.fetched[source_id] = RowState(
            "", 0.0, "", state.page_id, state.db_id
        )
