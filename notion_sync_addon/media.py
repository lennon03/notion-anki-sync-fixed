# -*- coding: utf-8 -*-
"""Media files: downloaded to a temp folder while reading Notion, copied
into the collection's media folder ahead of time when possible, and
imported when the collection is written.
"""

from __future__ import annotations

import filecmp
import os
import re
import shutil
import tempfile
from typing import Callable, Dict, List, Optional, Set, Tuple

from .htmlify import MediaCollector
from .notion_api import NotionClient
from .rows import Progress, SyncResult, _forget, _no_progress


def _download_media(
    client: NotionClient,
    media: MediaCollector,
    media_dir: str,
    progress: Progress = _no_progress,
) -> Tuple[List[Tuple[str, str]], Set[str]]:
    """Download every collected file straight to disk.

    A file that fails to download or write fails alone: it is left out and
    its partial file removed. Returns (downloaded (path, fname) pairs, names
    that failed).
    """
    out: List[Tuple[str, str]] = []
    failed: Set[str] = set()
    total = len(media.files)
    for index, (fname, url) in enumerate(media.files.items(), 1):
        progress(f"media {index}/{total}")
        ext = os.path.splitext(fname)[1].lower()
        path = os.path.join(media_dir, fname)
        try:
            client.download_file(url, path)
            if not ext:
                with open(path, "rb") as f:
                    ext = _sniff_ext(f.read(16))
                os.replace(path, path + ext)
                path += ext
        except Exception:  # noqa: BLE001 - media is best-effort only
            failed.add(fname)
            _remove_quietly(path)
            continue
        out.append(
            (path, fname + (ext if not os.path.splitext(fname)[1] else ""))
        )
    return out, failed


def _remove_quietly(path: str) -> None:
    """Remove a partly written file, if there is one."""
    try:
        os.remove(path)
    except OSError:
        pass


_PLACEHOLDER = re.compile(r"@@NSMEDIA:([A-Za-z0-9_.\-]+)@@")


def _forget_rows_with_failed_media(result: SyncResult) -> None:
    """Write rows whose media failed, but don't cache them (their text
    is current), but not cached, so the next sync downloads it again.

    This does not make the sync incomplete: which rows exist in Notion is
    known, so removing deleted rows stays safe.
    """
    if not result.failed_media:
        return
    names = sorted(result.failed_media)
    for page in result.pages:
        for table in page.tables:
            for row in table.rows:
                if (
                    row.unchanged
                    or not set(
                        _PLACEHOLDER.findall(row.back_html + row.context_html)
                    )
                    & result.failed_media
                ):
                    continue
                _forget(result, row.source_id)
    shown = ", ".join(names[:5]) + (
        f" and {len(names) - 5} more" if len(names) > 5 else ""
    )
    result.warnings.append(
        f"Could not download {len(names)} media file(s), "
        f"will retry next sync: {shown}"
    )


def _sniff_ext(blob: bytes) -> str:
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if blob[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return ".webp"
    if blob[:4] == b"OggS":
        return ".ogg"
    if blob[:3] == b"ID3" or blob[:2] == b"\xff\xfb":
        return ".mp3"
    if blob[:5] == b"%PDF-":
        return ".pdf"
    if blob[:2] == b"PK":
        return ".zip"
    return ".bin"


def _media_present(col) -> Tuple[str, Set[str]]:
    """Return the media folder and the exact names of its files."""
    try:
        folder = col.media.dir()
        return folder, set(os.listdir(folder))
    except (OSError, AttributeError):
        return "", set()


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.getsize(a) == os.path.getsize(b) and filecmp.cmp(
            a, b, shallow=False
        )
    except OSError:
        return False


def _stage_media(
    result: SyncResult,
    media_folder: str,
    should_stop: Optional[Callable[[], bool]] = None,
) -> None:
    """Copy downloaded files that are new into the media folder.

    Done by the fetch step, which does not hold the collection; the write
    step then finds each file under its name with the same bytes and skips
    it. A name that is already taken is left to Anki's ``add_file``, and a
    file that can't be copied is simply imported by the write step.
    """
    if not media_folder or not result.media_files:
        return
    try:
        taken = {n.casefold() for n in os.listdir(media_folder)}
    except OSError:
        return
    for path, fname in result.media_files:
        if should_stop is not None and should_stop():
            return
        name = os.path.split(fname)[-1]
        target = os.path.join(media_folder, name)
        if name.casefold() in taken or os.path.exists(target):
            continue
        tmp = ""
        try:
            fd, tmp = tempfile.mkstemp(
                prefix=".ns-stage-", suffix=".tmp", dir=media_folder
            )
            with os.fdopen(fd, "wb") as out, open(path, "rb") as src:
                shutil.copyfileobj(src, out)
            os.replace(tmp, target)
            tmp = ""
            taken.add(name.casefold())
        except OSError:
            pass
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def _import_media(col, result: SyncResult) -> Dict[str, str]:
    """Import downloaded files: Notion file name -> name in the collection.

    A re-downloaded file already in the media folder under exactly its name
    and with the same bytes is not handed to Anki again: that is what Anki
    would conclude too, after hashing and rewriting it while the
    collection is held.
    """
    imported_names: Dict[str, str] = {}
    folder, present = (
        _media_present(col) if result.media_files else ("", set())
    )
    for path, fname in result.media_files:
        name = os.path.split(fname)[-1]
        if name in present and _same_file(path, os.path.join(folder, name)):
            imported = name
        else:
            imported = col.media.add_file(path)
        imported_names[os.path.split(fname)[-1]] = imported
        imported_names.setdefault(os.path.splitext(fname)[0], imported)
    # a file that failed to download now may still be there from a past sync
    for fname in result.failed_media:
        try:
            if fname not in imported_names and col.media.have(fname):
                imported_names[fname] = fname
        except Exception:  # noqa: BLE001
            pass
    return imported_names
