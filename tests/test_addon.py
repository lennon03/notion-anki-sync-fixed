# -*- coding: utf-8 -*-
"""The add-on in Anki: running a sync, AnkiWeb, closing Anki."""

import os
from typing import List

import pytest

from tests.helpers import _Dialogs


def test_run_sync_end_to_end_without_modal_progress(addon, env):
    addon.mod.run_sync(manual=True)
    assert not addon.mod._state["busy"] and env.count() == 8
    assert len(addon.finished) == 1  # undo menu / screens refreshed once
    assert os.path.exists(
        env.sync_mod.cache_path(str(addon.mod.ADDON_DIR), "User 1")
    )
    addon.mod.run_sync(manual=True)
    assert (
        len(addon.finished) == 1
    )  # nothing changed: no undo step, no refresh
    assert any("8 unchanged" in m for m in addon.shown)


def test_every_failure_resets_busy(addon, env, monkeypatch):
    def boom(*a, **k):
        raise ValueError("unexpected")

    for target in ("fetch_pages", "prepare_sync", "apply_sync"):
        with monkeypatch.context() as m:
            m.setattr(env.sync_mod, target, boom)
            addon.mod.run_sync(manual=False)
        assert not addon.mod._state["busy"], target
        assert addon.statuses[-1] is None
    with monkeypatch.context() as m:  # a success callback that raises
        m.setattr(addon.mod, "_report", boom)
        addon.mod.run_sync(manual=False)
    assert not addon.mod._state["busy"]


def test_closing_profile_stops_before_import(addon, env, monkeypatch):
    real = env.sync_mod.fetch_pages

    def fetch_then_close(*a, **k):
        result = real(*a, **k)
        addon.mod._state["closing"] = True
        return result

    monkeypatch.setattr(env.sync_mod, "fetch_pages", fetch_then_close)
    addon.mod.run_sync(manual=False)
    assert env.count() == 0 and not addon.mod._state["busy"]


def test_late_progress_of_an_old_sync_is_ignored(addon, env):
    addon.mw.taskman.defer = True
    addon.mod.run_sync(manual=False, full=True)
    old = list(addon.queue)
    addon.queue.clear()
    assert old  # progress callbacks still queued
    addon.mod.run_sync(manual=False)  # newer sync starts
    addon.mod._state["busy"] = True  # ... and is still running
    addon.statuses.clear()
    for fn in old:
        fn()
    assert addon.statuses == []
    for fn in addon.queue:
        fn()
    assert addon.statuses  # the current sync's progress still shows


def test_ankiweb_followup_runs_once_and_never_loops(addon, env, monkeypatch):
    env.cfg["sync_with_ankiweb"] = True
    # skip the AnkiWeb status request; upload straight away
    monkeypatch.setattr(
        addon.mod, "_upload_to_ankiweb", addon.mod._start_followup_sync
    )
    addon.mod._on_ankiweb_sync_will_start()
    addon.mod._on_ankiweb_sync_finished()
    assert env.count() == 8
    assert addon.mw.sync_clicks == 1  # one upload, no Notion sync after it
    assert not addon.mod._state["followup_running"]
    assert not addon.mod._state["followup_pending"]


def test_failed_ankiweb_sync_does_not_start_notion(addon, env):
    env.cfg["sync_with_ankiweb"] = True
    addon.mod._on_ankiweb_sync_will_start()
    addon.mod._state["ankiweb_failed"] = True  # what the error probe records
    addon.mod._on_ankiweb_sync_finished()
    assert env.count() == 0


def test_followup_flags_cannot_get_stuck(addon, env):
    env.cfg["sync_with_ankiweb"] = True
    # Anki shows the media log instead of syncing: no hooks fire
    addon.mw.on_sync_button_clicked = lambda self=None: None
    addon.mod._start_followup_sync()
    assert not addon.mod._state["followup_pending"]
    # a follow-up whose sync_did_finish never came does not eat the next sync
    addon.mod._state["followup_running"] = True
    addon.mod._on_ankiweb_sync_will_start()
    addon.mod._on_ankiweb_sync_finished()
    assert env.count() == 8


def test_no_followup_without_saved_login(addon, env):
    addon.mw.pm.auth = None
    addon.mod._start_followup_sync()
    assert addon.mw.sync_clicks == 0


def test_sync_error_probe_flags_failure(monkeypatch):
    import types

    import notion_sync_addon as addon_mod

    fake = types.ModuleType("aqt.sync")
    calls = []
    setattr(fake, "handle_sync_error", lambda mw, err: calls.append(err))
    pkg = types.ModuleType("aqt")
    setattr(pkg, "sync", fake)
    monkeypatch.setitem(__import__("sys").modules, "aqt", pkg)
    monkeypatch.setitem(__import__("sys").modules, "aqt.sync", fake)
    monkeypatch.setitem(addon_mod._state, "ankiweb_failed", False)
    addon_mod._install_sync_error_probe()
    addon_mod._install_sync_error_probe()  # idempotent
    getattr(fake, "handle_sync_error")(None, "network down")
    assert calls == ["network down"] and addon_mod._state["ankiweb_failed"]


def test_missing_sync_error_hook_never_uploads_after_a_failed_status(
    addon, env, monkeypatch
):
    """Nit: without aqt.sync.handle_sync_error Notion still syncs, but the
    follow-up upload needs Anki's status check to ask for a normal sync."""
    import sys
    import types

    fake = types.ModuleType("aqt.sync")
    statuses: List[int] = []

    class Status:
        NO_CHANGES, NORMAL_SYNC, FULL_SYNC = 0, 1, 2

        def __init__(self, required):
            self.required = required

    setattr(
        fake,
        "get_sync_status",
        lambda mw, cb: [cb(Status(s)) for s in statuses],
    )
    pkg = types.ModuleType("aqt")
    setattr(pkg, "sync", fake)
    monkeypatch.setitem(sys.modules, "aqt", pkg)
    monkeypatch.setitem(sys.modules, "aqt.sync", fake)
    addon.mod._install_sync_error_probe()
    assert addon.mod._state["probe"] is False
    env.cfg["sync_with_ankiweb"] = True
    statuses[:] = []  # status check failed: Anki never calls back
    addon.mod._on_ankiweb_sync_will_start()
    addon.mod._on_ankiweb_sync_finished()
    assert env.count() == 8 and addon.mw.sync_clicks == 0
    statuses[:] = [Status.NO_CHANGES]
    addon.mod._upload_to_ankiweb()
    assert addon.mw.sync_clicks == 0
    statuses[:] = [Status.NORMAL_SYNC]
    addon.mod._upload_to_ankiweb()
    assert addon.mw.sync_clicks == 1


def test_closing_anki_waits_for_the_import(addon, env, monkeypatch):
    """N9: Anki unloads the collection only after the running apply ends."""
    dialogs = _Dialogs()
    monkeypatch.setattr(addon.mod, "_dialog_manager", lambda: dialogs)
    monkeypatch.setitem(addon.mod._state, "guard", None)
    events = []
    real_apply = env.sync_mod.apply_sync

    def apply_while_closing(col, result):
        dialogs.closeAll(
            lambda: events.append("collection closed")
        )  # user quits
        events.append("closing requested")
        stats = real_apply(col, result)
        events.append("cards written")
        return stats

    monkeypatch.setattr(env.sync_mod, "apply_sync", apply_while_closing)
    addon.mod.run_sync(manual=False)
    assert events == [
        "closing requested",
        "cards written",
        "collection closed",
    ]
    assert addon.mod._state["guard"] is None and env.count() == 8
    dialogs.closeAll(lambda: events.append("closed again"))  # nothing held now
    assert events[-1] == "closed again"


def test_import_failure_while_closing_is_quiet(addon, env, monkeypatch):
    def boom(col, result):
        addon.mod._state["closing"] = True
        raise RuntimeError("collection closed")

    monkeypatch.setattr(env.sync_mod, "apply_sync", boom)
    addon.mod.run_sync(manual=True)
    assert not any("failed" in m for m in addon.shown)
    assert not addon.mod._state["busy"]


def test_close_guard_keeps_only_the_first_callback(monkeypatch):
    addon = pytest.importorskip("notion_sync_addon")
    monkeypatch.setattr(addon, "tooltip", lambda *a, **k: None, raising=False)
    guard = addon._CloseGuard()
    calls = []
    guard.closeWithCallback(lambda: calls.append(1))
    guard.closeWithCallback(lambda: calls.append(2))
    assert len(guard.callbacks) == 1
    guard.callbacks[0]()
    assert calls == [1]
