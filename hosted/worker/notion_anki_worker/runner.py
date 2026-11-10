"""One run at a time, in a thread, and the schedule that starts them."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from notion_anki_worker import pipeline
from notion_anki_worker import store as st
from notion_anki_worker.crypto import DecryptError
from notion_anki_worker.notion_oauth import NotionOAuth, OAuthError

log = logging.getLogger(__name__)

SCHEDULE_TICK_SECONDS = 30
# runs that repeating can't fix: the owner has to act first
BLOCKING_STATUSES = (st.FULL_SYNC_NEEDED, st.AUTH_FAILED)


def renew_notion_sign_in(
    store: st.Store, oauth: Optional[NotionOAuth]
) -> Optional[str]:
    """Trade the stored refresh token for a new sign-in and store it.

    Returns the new access token, or None when there is nothing to renew
    with or Notion refuses.
    """
    refresh = store.get_secret(st.KEY_NOTION_REFRESH)
    if oauth is None or not refresh:
        return None
    try:
        tokens = oauth.refresh(refresh)
    except OAuthError:
        log.warning("Refreshing the Notion sign-in failed")
        return None
    store.put_secret(st.KEY_NOTION, tokens.access)
    store.put_secret(st.KEY_NOTION_REFRESH, tokens.refresh or refresh)
    return tokens.access


class NotReady(Exception):
    """Something the run needs was never set up, or can't be read."""


class Runner:
    """Starts runs in the background and records their outcome."""

    def __init__(
        self,
        store: st.Store,
        paths: pipeline.Paths,
        run_once: Callable[..., pipeline.RunOutcome] = pipeline.run_once,
        oauth: Optional[NotionOAuth] = None,
    ) -> None:
        self._oauth = oauth
        self._store = store
        self._paths = paths
        self._run_once = run_once
        self._busy = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        """Say whether a run is in progress."""
        return self._busy.locked()

    @contextmanager
    def idle(self) -> Iterator[bool]:
        """Hold off new runs for the ``with`` block.

        Yields False, and holds nothing, when a run is already in progress.
        """
        if not self._busy.acquire(blocking=False):
            yield False
            return
        try:
            yield True
        finally:
            self._busy.release()

    def ready(self) -> bool:
        """Say whether a run could start."""
        return (
            self._store.has(st.KEY_HKEY)
            and self._store.has(st.KEY_NOTION)
            and bool(self._store.pages())
        )

    def _request(self, full: bool) -> pipeline.RunRequest:
        store = self._store
        try:
            hkey = store.get_secret(st.KEY_HKEY)
            token = store.get_secret(st.KEY_NOTION)
        except DecryptError:
            raise NotReady() from None
        pages = [page["id"] for page in store.pages()]
        if not hkey or not token or not pages:
            raise NotReady()
        return pipeline.RunRequest(
            hkey=hkey,
            endpoint=store.get(st.KEY_ENDPOINT),
            notion_token=token,
            page_ids=pages,
            full=full,
        )

    def start(self, trigger: str, full: bool = False) -> Optional[int]:
        """Start a run. Returns its id, or None if one is running."""
        if not self._busy.acquire(blocking=False):
            return None
        try:
            request = self._request(full)
            run_id = self._store.start_run(trigger)
            self._thread = threading.Thread(
                target=self._execute, args=(run_id, request), daemon=True
            )
            self._thread.start()
        except BaseException:
            self._busy.release()
            raise
        return run_id

    def join(self, timeout: Optional[float] = None) -> None:
        """Wait for the current run to end (for tests)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _current_notion_token(self, token: str) -> str:
        """Refresh a Notion sign-in that has stopped working."""
        if self._oauth is None or self._oauth.token_works(token):
            return token
        return renew_notion_sign_in(self._store, self._oauth) or token

    def _execute(self, run_id: int, request: pipeline.RunRequest) -> None:
        store = self._store
        try:
            request.notion_token = self._current_notion_token(
                request.notion_token
            )
            outcome = self._run_once(
                request,
                self._paths,
                lambda line: store.append_log(run_id, line),
            )
            if outcome.endpoint:
                store.put(st.KEY_ENDPOINT, outcome.endpoint)
            store.finish_run(
                run_id,
                outcome.status,
                outcome.message,
                outcome.stats,
                outcome.warnings,
                outcome.backup,
            )
        except Exception:  # noqa: BLE001
            log.exception("Recording the run failed")
            store.finish_run(run_id, st.FAILED, "The run failed.")
        finally:
            self._busy.release()


class Scheduler:
    """Starts a run whenever the configured interval has passed.

    The loop lives in this process, so nothing fires while the host has it
    asleep; an overdue run starts at the first tick after it wakes.
    """

    def __init__(self, store: st.Store, runner: Runner) -> None:
        self._store = store
        self._runner = runner
        self._stop = threading.Event()

    def next_run_at(self) -> Optional[float]:
        """Return when the next scheduled run is due, if one will be."""
        minutes = self._store.schedule_minutes()
        if not minutes or not self._runner.ready():
            return None
        last = self._store.latest_run()
        if last is None:
            return time.time()
        if last["status"] in BLOCKING_STATUSES:
            return None
        return float(last["started_at"]) + minutes * 60

    def tick(self) -> None:
        """Start a run if one is due."""
        due = self.next_run_at()
        if due is not None and due <= time.time():
            try:
                self._runner.start("schedule")
            except NotReady:
                # e.g. a secret stored under another key: the start-up log
                # says so once, and the owner has to reconnect
                pass

    def run_forever(self) -> None:
        """Tick until ``stop``."""
        while not self._stop.wait(SCHEDULE_TICK_SECONDS):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the loop must survive
                log.exception("Scheduler tick failed")

    def start(self) -> None:
        """Run the loop in a daemon thread."""
        threading.Thread(target=self.run_forever, daemon=True).start()

    def stop(self) -> None:
        """Stop the loop."""
        self._stop.set()
