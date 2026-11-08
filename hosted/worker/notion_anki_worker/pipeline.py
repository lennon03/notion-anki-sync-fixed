"""One sync: AnkiWeb -> scratch collection -> Notion -> AnkiWeb."""

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
from typing import Callable, Dict, List, Optional, Set

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
MEDIA_PENDING = "pending"  # marker: the media store has unsent files
BACKUP_KEEP = 5
BACKUP_NAME = re.compile(r"before-\d{8}-\d{6}-[0-9a-f]{6}\.colpkg")
MEDIA_SYNC_TIMEOUT_SECONDS = 60 * 60
MEDIA_POLL_SECONDS = 1.0
PROGRESS_EVERY_SECONDS = 5.0

Say = Callable[[str], None]

FULL_SYNC_MESSAGE = (
    "AnkiWeb needs a full sync, so nothing was changed. Sync from Anki "
    "desktop and choose which side to keep, then run Sync now."
)
AUTH_MESSAGE = "AnkiWeb no longer accepts this login. Reconnect AnkiWeb."


class FullSyncNeeded(Exception):
    """AnkiWeb can't take a normal sync: stop without writing."""


@dataclass(frozen=True)
class Paths:
    """Where the worker keeps things on the volume."""

    root: Path

    @property
    def scratch(self) -> Path:
        """Parent of the per-run folders."""
        return self.root / "scratch"

    @property
    def media(self) -> Path:
        """Media folder and its database between runs."""
        return self.root / "media"

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
            self.media,
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
    media_pending: Optional[bool] = None


def addon_config(token: str, page_ids: List[str]) -> dict:
    """Build the add-on's config dict: its defaults plus token and pages."""
    addon_dir = Path(addon_sync.__file__).parent
    config = json.loads(
        (addon_dir / "config.json").read_text(encoding="utf-8")
    )
    config["notion_token"] = token
    config["notion_pages"] = [{"page_id": p} for p in page_ids]
    return config


def _move_media_in(paths: Paths, scratch: Path) -> None:
    """Bring the media store into the run, never the database alone."""
    folder, database = paths.media / MEDIA_FOLDER, paths.media / MEDIA_DB
    if not folder.is_dir():
        # A database without its files would report them deleted to AnkiWeb
        database.unlink(missing_ok=True)
        return
    os.replace(folder, scratch / MEDIA_FOLDER)
    if database.is_file():
        os.replace(database, scratch / MEDIA_DB)


def move_media_out(paths: Paths, scratch: Path) -> None:
    """Put a run's media back into the store, if the run has it.

    The folder alone is fine: its files just count as new to Anki.
    """
    folder, database = scratch / MEDIA_FOLDER, scratch / MEDIA_DB
    if folder.is_dir():
        os.replace(folder, paths.media / MEDIA_FOLDER)
        if database.is_file():
            os.replace(database, paths.media / MEDIA_DB)


def recover(paths: Paths) -> None:
    """After a crash: restore media, delete the leftover scratch folders."""
    paths.ensure()
    for scratch in paths.scratch.iterdir():
        if scratch.is_dir():
            try:
                move_media_out(paths, scratch)
            except OSError:
                log.exception("Could not restore media from a crashed run")
            shutil.rmtree(scratch, ignore_errors=True)


def _close(col: Optional[Collection]) -> None:
    if col is None or col.db is None:
        return
    try:
        col.close()
    except Exception:  # noqa: BLE001 - the scratch folder is deleted anyway
        log.warning("Closing the scratch collection failed", exc_info=True)


def _download(col: Collection, auth: SyncAuth) -> None:
    """Replace the empty scratch collection with the AnkiWeb one."""
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


def _throttled(say: Say) -> Say:
    """Report row-by-row progress only every few seconds."""
    last = [0.0]

    def report(message: str) -> None:
        now = time.monotonic()
        if now - last[0] >= PROGRESS_EVERY_SECONDS:
            last[0] = now
            say(message)

    return report


def _run(
    request: RunRequest,
    paths: Paths,
    scratch: Path,
    before: Set[str],
    say: Say,
) -> RunOutcome:
    col: Optional[Collection] = None
    result = None
    endpoint = request.endpoint
    try:
        col = Collection(str(scratch / COLLECTION))
        auth = SyncAuth(hkey=request.hkey, endpoint=endpoint)

        say("Asking AnkiWeb for the collection")
        out = col.sync_collection(auth, sync_media=False)
        if out.new_endpoint:
            endpoint = out.new_endpoint
            auth = SyncAuth(hkey=request.hkey, endpoint=endpoint)
        if out.required != out.FULL_DOWNLOAD:
            raise FullSyncNeeded()
        say("Downloading the collection")
        _download(col, auth)

        staged_cache = scratch / "notion-cache.json"
        prepared = addon_sync.prepare_sync(col, str(paths.cache), request.full)

        say("Reading Notion")
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

        backup_file = _backup(col, scratch)
        modified_before = col.mod
        say("Writing cards")
        stats = addon_sync.apply_sync(col, result)
        changed = col.mod != modified_before

        backup_name = None
        if changed:
            say("Sending changes to AnkiWeb")
            pushed = col.sync_collection(auth, sync_media=False)
            if pushed.new_endpoint:
                endpoint = pushed.new_endpoint
                auth = SyncAuth(hkey=request.hkey, endpoint=endpoint)
            if pushed.required != pushed.NO_CHANGES:
                raise FullSyncNeeded()
            backup_name = _store_backup(backup_file, paths)
        else:
            say("Nothing changed: AnkiWeb left as it is")
        if staged_cache.exists():
            os.replace(staged_cache, paths.cache)

        warnings = list(result.warnings)
        marker = paths.media / MEDIA_PENDING
        media_pending = (
            marker.exists() or _media_names(scratch / MEDIA_FOLDER) != before
        )
        if media_pending:
            say("Syncing media")
            try:
                _sync_media(col, auth)
                media_pending = False
                marker.unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                log.warning("Media sync failed", exc_info=True)
                warnings.append(
                    f"Media was not sent to AnkiWeb and will be retried: {e}"
                )

        return RunOutcome(
            status=WARNINGS if warnings else OK,
            message=_summary(stats, result.incomplete),
            stats=stats,
            warnings=warnings,
            backup=backup_name,
            endpoint=endpoint,
            media_pending=media_pending,
        )
    finally:
        if result is not None:
            result.cleanup()
        _close(col)


def _summary(stats: Dict[str, int], incomplete: bool) -> str:
    parts = [
        f"{stats[key]} {key}"
        for key in ("added", "updated", "removed", "unchanged")
        if stats.get(key)
    ]
    text = ", ".join(parts) or "Nothing to sync"
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
    outcome: Optional[RunOutcome] = None
    before: Set[str] = set()
    try:
        _move_media_in(paths, scratch)
        before = _media_names(scratch / MEDIA_FOLDER)
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
        unsent = _media_names(scratch / MEDIA_FOLDER) != before
        if unsent and (outcome is None or outcome.media_pending is not False):
            (paths.media / MEDIA_PENDING).touch()
        restored = True
        try:
            move_media_out(paths, scratch)
        except OSError:
            # keep the folder: start-up recovery restores it
            log.exception("Could not move media back")
            restored = False
        if restored:
            shutil.rmtree(scratch, ignore_errors=True)
