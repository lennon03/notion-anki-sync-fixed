"""SQLite on the volume: encrypted secrets, settings and run history."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from notion_anki_worker.crypto import DecryptError, Vault
from notion_anki_worker.redact import register, scrub

log = logging.getLogger(__name__)

KEY_HKEY = "ankiweb_hkey"
KEY_NOTION = "notion_token"
KEY_NOTION_REFRESH = "notion_refresh_token"
KEY_NOTION_WORKSPACE = "notion_workspace"
KEY_ENDPOINT = "ankiweb_endpoint"
KEY_PAGES = "notion_pages"
KEY_SCHEDULE = "schedule_minutes"
KEY_OPTIONS = "sync_options"

MAX_RUNS = 50
MAX_LOG_LINES = 500
LOG_HEAD_LINES = 100  # a long log keeps its start and its end

RUNNING = "running"
OK = "ok"
WARNINGS = "warnings"
FAILED = "failed"
FULL_SYNC_NEEDED = "full_sync_needed"
AUTH_FAILED = "auth_failed"
INTERRUPTED = "interrupted"

_SCHEMA = """
create table if not exists settings (
    key text primary key,
    value text not null
);
create table if not exists runs (
    id integer primary key autoincrement,
    trigger text not null,
    status text not null,
    started_at real not null,
    finished_at real,
    message text not null default '',
    stats text not null default '{}',
    warnings text not null default '[]',
    log text not null default '[]',
    backup text
);
"""


def _shorten_log(lines: List[Dict[str, Any]]) -> None:
    """Keep a log at ``MAX_LOG_LINES``: its start, a marker, its end.

    The marker stands for every line left out so far.
    """
    excess = len(lines) - MAX_LOG_LINES
    if excess <= 0:
        return
    at = LOG_HEAD_LINES
    # the line at ``at`` is the marker, or becomes it
    left_out = lines[at].get("left_out", 1) + excess
    del lines[at + 1 : at + 1 + excess]
    lines[at] = {
        "at": lines[at]["at"],
        "text": f"... {left_out} lines left out ...",
        "left_out": left_out,
    }


class Store:
    """One connection per call, serialized by a lock; runs are few."""

    def __init__(self, path: Path, vault: Vault) -> None:
        self._path = path
        self._vault = vault
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as db:
            db.execute("pragma journal_mode=wal")
            db.executescript(_SCHEMA)
        self._register_known_secrets()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _register_known_secrets(self) -> None:
        for name in (KEY_HKEY, KEY_NOTION, KEY_NOTION_REFRESH):
            try:
                value = self.get_secret(name)
            except DecryptError:
                log.error("A stored secret can't be read with this key")
                continue
            if value:
                register(value)

    # settings ----------------------------------------------------------

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Return a plain setting."""
        with self._lock, self._connect() as db:
            row = db.execute(
                "select value from settings where key=?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def put(self, key: str, value: str) -> None:
        """Store a plain setting."""
        with self._lock, self._connect() as db:
            db.execute(
                "insert into settings(key,value) values(?,?) "
                "on conflict(key) do update set value=excluded.value",
                (key, value),
            )

    def delete(self, *keys: str) -> None:
        """Remove settings."""
        with self._lock, self._connect() as db:
            for key in keys:
                db.execute("delete from settings where key=?", (key,))

    def get_secret(self, key: str) -> Optional[str]:
        """Return a decrypted secret, or None."""
        stored = self.get(key)
        return self._vault.decrypt(key, stored) if stored else None

    def put_secret(self, key: str, value: str) -> None:
        """Encrypt and store a secret."""
        register(value)
        self.put(key, self._vault.encrypt(key, value))

    def has(self, key: str) -> bool:
        """Say whether a setting exists, without decrypting it."""
        return self.get(key) is not None

    def pages(self) -> List[Dict[str, str]]:
        """Return the configured pages as ``{id, title}`` entries."""
        return json.loads(self.get(KEY_PAGES) or "[]")

    def schedule_minutes(self) -> int:
        """Return the schedule; 0 means manual only."""
        return int(self.get(KEY_SCHEDULE) or 0)

    def options(self) -> Dict[str, Any]:
        """Return the add-on options set for the hosted sync."""
        return json.loads(self.get(KEY_OPTIONS) or "{}")

    def forget_credentials(self) -> None:
        """Delete the host key, its endpoint and the Notion token."""
        self.delete(
            KEY_HKEY,
            KEY_ENDPOINT,
            KEY_NOTION,
            KEY_NOTION_REFRESH,
            KEY_NOTION_WORKSPACE,
        )

    # runs --------------------------------------------------------------

    def start_run(self, trigger: str) -> int:
        """Record a new running run and return its id."""
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "insert into runs(trigger,status,started_at) values(?,?,?)",
                (trigger, RUNNING, time.time()),
            )
            db.execute(
                "delete from runs where id not in "
                "(select id from runs order by id desc limit ?)",
                (MAX_RUNS,),
            )
            return int(cursor.lastrowid or 0)

    def append_log(self, run_id: int, line: str) -> None:
        """Add a scrubbed line to a run's log."""
        with self._lock, self._connect() as db:
            row = db.execute(
                "select log from runs where id=?", (run_id,)
            ).fetchone()
            if row is None:
                return
            lines = json.loads(row["log"])
            lines.append({"at": time.time(), "text": scrub(line)})
            _shorten_log(lines)
            db.execute(
                "update runs set log=? where id=?",
                (json.dumps(lines), run_id),
            )

    def finish_run(
        self,
        run_id: int,
        status: str,
        message: str = "",
        stats: Optional[Dict[str, int]] = None,
        warnings: Optional[List[str]] = None,
        backup: Optional[str] = None,
    ) -> None:
        """Record how a run ended; text is scrubbed first."""
        with self._lock, self._connect() as db:
            db.execute(
                "update runs set status=?, finished_at=?, message=?, "
                "stats=?, warnings=?, backup=? where id=?",
                (
                    status,
                    time.time(),
                    scrub(message),
                    json.dumps(stats or {}),
                    json.dumps([scrub(w) for w in warnings or []]),
                    backup,
                    run_id,
                ),
            )

    def mark_interrupted(self) -> int:
        """Close runs left open by a crash. Returns how many."""
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "update runs set status=?, finished_at=?, message=? "
                "where status=?",
                (
                    INTERRUPTED,
                    time.time(),
                    "The worker restarted during this run.",
                    RUNNING,
                ),
            )
            return cursor.rowcount

    @staticmethod
    def _run_dict(row: sqlite3.Row) -> Dict[str, Any]:
        run = dict(row)
        for key in ("stats", "warnings", "log"):
            run[key] = json.loads(run[key])
        return run

    def latest_run(self) -> Optional[Dict[str, Any]]:
        """Return the newest run with its log."""
        runs = self.recent_runs(1, with_log=True)
        return runs[0] if runs else None

    def recent_runs(
        self, limit: int = 20, with_log: bool = False
    ) -> List[Dict[str, Any]]:
        """Return the newest runs, newest first."""
        with self._lock, self._connect() as db:
            rows = db.execute(
                "select * from runs order by id desc limit ?", (limit,)
            ).fetchall()
        runs = [self._run_dict(r) for r in rows]
        if not with_log:
            for run in runs:
                del run["log"]
        return runs
