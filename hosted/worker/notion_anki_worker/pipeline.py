"""One sync: AnkiWeb -> local collection -> Notion -> AnkiWeb."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from anki.collection import Collection, SyncAuth
from anki.errors import SyncError, SyncErrorKind
from notion_anki_worker.store import (
    AUTH_FAILED,
    FAILED,
    FULL_SYNC_NEEDED,
    OK,
    WARNINGS,
)

from notion_sync_addon import sync as addon_sync
from notion_sync_addon.notion_api import NotionError

log = logging.getLogger(__name__)

COLLECTION = "collection.anki2"
MEDIA_FOLDER = "collection.media"
MEDIA_DB = "collection.media.db2"
MEDIA_PENDING = "pending"  # marker: the media folder has unsent files
DIRTY = "dirty"  # marker: the local collection may differ from AnkiWeb
RUN_STATE = "run-state.json"
BACKUP_KEEP = 5
BACKUP_NAME = re.compile(r"before-\d{8}-\d{6}-[0-9a-f]{6}\.colpkg")
MEDIA_SYNC_TIMEOUT_SECONDS = 60 * 60
MEDIA_POLL_SECONDS = 1.0
PROGRESS_EVERY_SECONDS = 5.0
# Notion's edit times are rounded to the minute
NOTION_EDIT_MARGIN_SECONDS = 120
# Some content changes without moving any edit time (see the add-on's
# volatile rows), so Notion is read in full at least this often
FULL_CHECK_SECONDS = addon_sync.VOLATILE_MAX_AGE_SECONDS

Say = Callable[[str], None]

FULL_SYNC_MESSAGE = (
    "AnkiWeb needs a full sync, so nothing was changed. Sync from Anki "
    "desktop and choose which side to keep, then run Sync now."
)
AUTH_MESSAGE = "AnkiWeb no longer accepts this login. Reconnect AnkiWeb."
NOTHING = "Nothing to sync"


class FullSyncNeeded(Exception):
    """AnkiWeb can't take a normal sync: stop without writing."""


@dataclass(frozen=True)
class Paths:
    """Where the worker keeps things on the volume."""

    root: Path

    @property
    def scratch(self) -> Path:
        """Parent of the per-run temporary folders."""
        return self.root / "scratch"

    @property
    def profile(self) -> Path:
        """The local collection and its media, kept between runs."""
        return self.root / "profile"

    @property
    def backups(self) -> Path:
        """Collection exports from before each run that changed something."""
        return self.root / "backups"

    @property
    def cache(self) -> Path:
        """The add-on's row cache."""
        return self.root / "cache" / "notion-cache.json"

    def ensure(self) -> None:
        """Create the folders."""
        for path in (
            self.scratch,
            self.profile,
            self.backups,
            self.cache.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass
class RunRequest:
    """What a run needs from the store."""

    hkey: str = field(repr=False)
    endpoint: Optional[str]
    notion_token: str = field(repr=False)
    page_ids: List[str]
    full: bool = False


@dataclass
class RunOutcome:
    """How a run ended, and what the store must remember."""

    status: str
    message: str = ""
    stats: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    backup: Optional[str] = None
    endpoint: Optional[str] = None
    media_pending: Optional[bool] = None  # None: the run stopped before media


class _Session:
    """The open collection and the AnkiWeb login that goes with it."""

    def __init__(self, request: RunRequest) -> None:
        self.hkey = request.hkey
        self.endpoint = request.endpoint
        self.col: Optional[Collection] = None

    @property
    def auth(self) -> SyncAuth:
        """The login to use for the next AnkiWeb call."""
        return SyncAuth(hkey=self.hkey, endpoint=self.endpoint)

    def moved_to(self, endpoint: str) -> None:
        """Remember that AnkiWeb moved this account to another host."""
        if endpoint:
            self.endpoint = endpoint

    def close(self) -> None:
        """Close the collection, if open."""
        col, self.col = self.col, None
        if col is None or col.db is None:
            return
        try:
            col.close()
        except Exception:  # noqa: BLE001 - a dirty marker covers a bad close
            log.warning("Closing the collection failed", exc_info=True)


def addon_config(token: str, page_ids: List[str]) -> dict:
    """Build the add-on's config dict: its defaults plus token and pages."""
    addon_dir = Path(addon_sync.__file__).parent
    config = json.loads(
        (addon_dir / "config.json").read_text(encoding="utf-8")
    )
    config["notion_token"] = token
    config["notion_pages"] = [{"page_id": p} for p in page_ids]
    return config


def _migrate_legacy_media(paths: Paths) -> None:
    """Move media from the folder older versions kept it in."""
    legacy = paths.root / "media"
    if not legacy.is_dir():
        return
    for name in (MEDIA_FOLDER, MEDIA_DB, MEDIA_PENDING):
        source = legacy / name
        if source.exists() and not (paths.profile / name).exists():
            os.replace(source, paths.profile / name)
    shutil.rmtree(legacy, ignore_errors=True)


def recover(paths: Paths) -> None:
    """At start-up: finish migrations, delete leftover temporary folders."""
    paths.ensure()
    _migrate_legacy_media(paths)
    for scratch in paths.scratch.iterdir():
        if scratch.is_dir():
            shutil.rmtree(scratch, ignore_errors=True)


def forget_collection(paths: Paths) -> None:
    """Delete the local copy; used when the AnkiWeb account changes."""
    shutil.rmtree(paths.profile, ignore_errors=True)
    paths.cache.unlink(missing_ok=True)
    paths.ensure()


def _discard_collection(paths: Paths) -> None:
    """Drop the local collection. AnkiWeb has everything that matters."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        (paths.profile / (COLLECTION + suffix)).unlink(missing_ok=True)
    (paths.profile / RUN_STATE).unlink(missing_ok=True)
    (paths.profile / DIRTY).unlink(missing_ok=True)


def _open_collection(paths: Paths) -> tuple[Collection, bool]:
    """Open the local collection; True if it existed and can be trusted."""
    if not (paths.profile / MEDIA_FOLDER).is_dir():
        # A database without its files would report them deleted to AnkiWeb
        (paths.profile / MEDIA_DB).unlink(missing_ok=True)
    if (paths.profile / DIRTY).exists():
        _discard_collection(paths)
    path = paths.profile / COLLECTION
    existed = path.exists()
    return Collection(str(path)), existed


def _download(col: Collection, auth: SyncAuth) -> None:
    """Replace the local collection with the AnkiWeb one."""
    col.close_for_full_sync()
    try:
        # The only full transfer in the worker. The upload direction is
        # never used: see docs/hosted-sync.md.
        col.full_upload_or_download(auth=auth, server_usn=None, upload=False)
    except BaseException:
        try:
            col.reopen(after_full_sync=True)
        except Exception:  # noqa: BLE001 - the download error matters more
            log.warning("Reopening after a failed download", exc_info=True)
        raise
    col.reopen(after_full_sync=True)


def _bring_up_to_date(
    session: _Session, existed: bool, paths: Paths, say: Say
) -> None:
    """Pull AnkiWeb's changes, downloading only when there is no other way."""
    assert session.col is not None
    say("Getting changes from AnkiWeb")
    out = session.col.sync_collection(session.auth, sync_media=False)
    session.moved_to(out.new_endpoint)
    if existed and out.required == out.NO_CHANGES:
        return
    if existed and out.required != out.FULL_DOWNLOAD:
        # AnkiWeb's copy was replaced, e.g. by a one-way sync from desktop.
        # The local copy holds nothing of its own, so start again from it.
        say("AnkiWeb's collection was replaced: getting it again")
        session.close()
        _discard_collection(paths)
        session.col = Collection(str(paths.profile / COLLECTION))
        out = session.col.sync_collection(session.auth, sync_media=False)
        session.moved_to(out.new_endpoint)
    if out.required != out.FULL_DOWNLOAD:
        raise FullSyncNeeded()
    say("Downloading the collection")
    (paths.profile / DIRTY).touch()
    _download(session.col, session.auth)
    (paths.profile / DIRTY).unlink(missing_ok=True)


def _notes_signature(col: Collection) -> List[int]:
    """Change whenever a note is added, edited or deleted (not reviewed)."""
    assert col.db is not None
    row = col.db.first("select count(), coalesce(max(mod), 0) from notes")
    assert row is not None
    return [int(row[0]), int(row[1])]


def _load_state(paths: Paths) -> Dict[str, Any]:
    try:
        state = json.loads(
            (paths.profile / RUN_STATE).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _notion_is_quiet(token: str, since: float) -> bool:
    """Say whether nothing visible to the token was edited since ``since``.

    Notion's search can lag, which is safe: the time to compare with only
    moves on after a run that read Notion for real, so a late edit is
    still newer than it when it finally shows up.
    """
    try:
        found = addon_sync.NotionClient(token)._request(
            "POST",
            "/search",
            {
                "sort": {
                    "timestamp": "last_edited_time",
                    "direction": "descending",
                },
                "page_size": 1,
            },
        )
    except NotionError:
        return False
    results = found.get("results") or []
    if not results:
        return False
    latest = addon_sync._parse_notion_time(
        results[0].get("last_edited_time") or ""
    )
    return latest is not None and (latest < since - NOTION_EDIT_MARGIN_SECONDS)


def _nothing_to_do(
    request: RunRequest, state: Dict[str, Any], col: Collection
) -> bool:
    """Say whether this run can stop after asking Notion one question."""
    if request.full or not state:
        return False
    if (
        state.get("pages") != sorted(request.page_ids)
        or state.get("render") != addon_sync.RENDER_VERSION
        or state.get("notes") != _notes_signature(col)
        or time.time() - float(state.get("full_at", 0)) >= FULL_CHECK_SECONDS
    ):
        return False
    return _notion_is_quiet(
        request.notion_token, float(state.get("watermark", 0))
    )


def _keep_unselected(result: Any, sources: Set[str]) -> None:
    """Keep the cards of pages that aren't selected (any more).

    The add-on deletes a synced note it didn't see. Here not seeing a note
    can just mean its page was turned off, and deleting would lose its
    review history. Only a row last seen in a selected page may go.
    """
    for source in sources:
        origin = addon_sync._origin(source, result)
        if origin is None or origin.page_id not in result.page_ids:
            result.protected.add(source)


def _backup(col: Collection, scratch: Path) -> Path:
    """Export the collection into the scratch folder, then reopen it."""
    target = scratch / "backup.colpkg"
    col.export_collection_package(
        str(target), include_media=False, legacy=False
    )
    col.reopen()
    return target


def _store_backup(path: Path, paths: Paths) -> str:
    """Move a backup into place and drop all but the newest few."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    name = f"before-{stamp}-{uuid.uuid4().hex[:6]}.colpkg"
    os.replace(path, paths.backups / name)
    for old in list_backups(paths)[BACKUP_KEEP:]:
        (paths.backups / old["name"]).unlink(missing_ok=True)
    return name


def list_backups(paths: Paths) -> List[Dict]:
    """Return the backups, newest first."""
    out: List[Dict] = []
    if not paths.backups.is_dir():
        return out
    for entry in paths.backups.iterdir():
        if BACKUP_NAME.fullmatch(entry.name):
            stat = entry.stat()
            out.append(
                {
                    "name": entry.name,
                    "size": stat.st_size,
                    "created_at": stat.st_mtime,
                }
            )
    out.sort(key=lambda b: b["name"], reverse=True)
    return out


def _media_names(folder: Path) -> Set[str]:
    return set(os.listdir(folder)) if folder.is_dir() else set()


def _sync_media(col: Collection, auth: SyncAuth) -> None:
    col.sync_media(auth)
    deadline = time.monotonic() + MEDIA_SYNC_TIMEOUT_SECONDS
    # media_sync_status raises when the sync failed
    while col.media_sync_status().active:
        if time.monotonic() > deadline:
            col.abort_media_sync()
            raise TimeoutError("media sync timed out")
        time.sleep(MEDIA_POLL_SECONDS)


def _send_media(
    session: _Session,
    paths: Paths,
    before: Set[str],
    say: Say,
    warnings: List[str],
) -> bool:
    """Send new media if there is any. True if some is still unsent."""
    assert session.col is not None
    marker = paths.profile / MEDIA_PENDING
    if not marker.exists() and (
        _media_names(paths.profile / MEDIA_FOLDER) == before
    ):
        return False
    say("Syncing media")
    try:
        _sync_media(session.col, session.auth)
    except Exception as e:  # noqa: BLE001
        log.warning("Media sync failed", exc_info=True)
        warnings.append(
            f"Media was not sent to AnkiWeb and will be retried: {e}"
        )
        return True
    marker.unlink(missing_ok=True)
    return False


def _throttled(say: Say) -> Say:
    """Report row-by-row progress only every few seconds."""
    last = [0.0]

    def report(message: str) -> None:
        now = time.monotonic()
        if now - last[0] >= PROGRESS_EVERY_SECONDS:
            last[0] = now
            say(message)

    return report


class _Laps:
    """Measures each step, to show where a run spends its time."""

    def __init__(self) -> None:
        self.parts: List[str] = []
        self._last = time.monotonic()

    def mark(self, name: str) -> None:
        """End the current step, calling it ``name``."""
        now = time.monotonic()
        self.parts.append(f"{name} {now - self._last:.1f}s")
        self._last = now

    def __str__(self) -> str:
        return "Timings: " + ", ".join(self.parts)


def _run(
    request: RunRequest,
    paths: Paths,
    scratch: Path,
    before: Set[str],
    say: Say,
) -> RunOutcome:
    session = _Session(request)
    result = None
    laps = _Laps()
    try:
        session.col, existed = _open_collection(paths)
        _bring_up_to_date(session, existed, paths, say)
        laps.mark("AnkiWeb")
        col = session.col
        assert col is not None

        if _nothing_to_do(request, _load_state(paths), col):
            laps.mark("check")
            say("Nothing changed in Notion or in your notes")
            warnings: List[str] = []
            pending = _send_media(session, paths, before, say, warnings)
            say(str(laps))
            return RunOutcome(
                status=WARNINGS if warnings else OK,
                message=NOTHING,
                warnings=warnings,
                endpoint=session.endpoint,
                media_pending=pending,
            )

        staged_cache = scratch / "notion-cache.json"
        prepared = addon_sync.prepare_sync(col, str(paths.cache), request.full)
        say("Reading Notion")
        read_at = time.time()
        result = addon_sync.fetch_pages(
            addon_config(request.notion_token, request.page_ids),
            prepared.known,
            _throttled(say),
            prepared.structure,
            prepared.previous,
            prepared.sources,
            media_folder=prepared.media_folder,
        )
        result.cache_path = str(staged_cache)
        _keep_unselected(result, prepared.sources)
        laps.mark("Notion")

        backup_file = _backup(col, scratch)
        modified_before = col.mod
        laps.mark("backup")
        say("Writing cards")
        (paths.profile / DIRTY).touch()
        stats = addon_sync.apply_sync(col, result)
        changed = col.mod != modified_before
        laps.mark("cards")

        backup_name = None
        if changed:
            say("Sending changes to AnkiWeb")
            pushed = col.sync_collection(session.auth, sync_media=False)
            session.moved_to(pushed.new_endpoint)
            if pushed.required != pushed.NO_CHANGES:
                raise FullSyncNeeded()
            backup_name = _store_backup(backup_file, paths)
            laps.mark("push")
        else:
            say("Nothing changed: AnkiWeb left as it is")
        (paths.profile / DIRTY).unlink(missing_ok=True)
        if staged_cache.exists():
            os.replace(staged_cache, paths.cache)

        warnings = list(result.warnings)
        if not warnings and not result.incomplete:
            (paths.profile / RUN_STATE).write_text(
                json.dumps(
                    {
                        "watermark": read_at,
                        "full_at": read_at,
                        "pages": sorted(request.page_ids),
                        "render": addon_sync.RENDER_VERSION,
                        "notes": _notes_signature(col),
                    }
                ),
                encoding="utf-8",
            )
        else:  # read everything again next time
            (paths.profile / RUN_STATE).unlink(missing_ok=True)

        pending = _send_media(session, paths, before, say, warnings)
        laps.mark("media")
        say(str(laps))
        return RunOutcome(
            status=WARNINGS if warnings else OK,
            message=_summary(stats, result.incomplete),
            stats=stats,
            warnings=warnings,
            backup=backup_name,
            endpoint=session.endpoint,
            media_pending=pending,
        )
    finally:
        if result is not None:
            result.cleanup()
        session.close()


def _summary(stats: Dict[str, int], incomplete: bool) -> str:
    parts = [
        f"{stats[key]} {key}"
        for key in ("added", "updated", "removed", "unchanged")
        if stats.get(key)
    ]
    text = ", ".join(parts) or NOTHING
    if incomplete:
        text += " (cards of the parts that could not be read were kept)"
    return text


def _is_full_sync_error(error: SyncError) -> bool:
    # AnkiWeb reports a schema mismatch found mid-sync as a sync error
    return "full sync" in str(error).lower()


def run_once(request: RunRequest, paths: Paths, say: Say) -> RunOutcome:
    """Run one sync. Errors become an outcome; nothing is raised."""
    paths.ensure()
    scratch = Path(tempfile.mkdtemp(prefix="run-", dir=paths.scratch))
    folder = paths.profile / MEDIA_FOLDER
    before = _media_names(folder)
    outcome: Optional[RunOutcome] = None
    try:
        outcome = _run(request, paths, scratch, before, say)
        return outcome
    except FullSyncNeeded:
        return RunOutcome(FULL_SYNC_NEEDED, FULL_SYNC_MESSAGE)
    except SyncError as e:
        if e.kind is SyncErrorKind.AUTH:
            return RunOutcome(AUTH_FAILED, AUTH_MESSAGE)
        if _is_full_sync_error(e):
            return RunOutcome(FULL_SYNC_NEEDED, FULL_SYNC_MESSAGE)
        return RunOutcome(FAILED, f"AnkiWeb: {e}")
    except NotionError as e:
        return RunOutcome(FAILED, f"Notion: {e.reason()}")
    except Exception as e:  # noqa: BLE001
        log.exception("Run failed")
        return RunOutcome(FAILED, f"Sync failed ({type(e).__name__}).")
    finally:
        # Files that arrived but weren't sent, whatever stopped the run: the
        # rows may be cached by then, so nothing else would send them later.
        if _media_names(folder) != before and (
            outcome is None or outcome.media_pending is not False
        ):
            (paths.profile / MEDIA_PENDING).touch()
        shutil.rmtree(scratch, ignore_errors=True)
