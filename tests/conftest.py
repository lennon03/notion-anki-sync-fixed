# -*- coding: utf-8 -*-
"""Fixtures every test module can use without importing them: ``env``,
``addon``, ``qapp`` and ``frozen_now``. Fakes and builders such as
``FakeNotion``, ``_rid``, ``_para`` and ``P1``/``DB1`` are in
``tests.helpers``.
"""

import os
import time
from types import SimpleNamespace
from typing import Any, List

import pytest

from tests.helpers import (
    DB1,
    DB2,
    DB3,
    EDITED_TS,
    OLD,
    P1,
    P2,
    FakeNotion,
    _para,
    _rid,
)


@pytest.fixture
def frozen_now(monkeypatch):
    """Pin the sync's clock an hour after EDITED (cache entries stay fresh)."""
    monkeypatch.setattr(time, "time", lambda: EDITED_TS + 3600)
    return EDITED_TS + 3600


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Give a temp collection and a fake Notion with two pages and three
    tables; ``env.sync()`` runs prepare -> fetch -> apply like the add-on.
    """
    collection = pytest.importorskip("anki.collection")
    import notion_sync_addon.fetch as fetch_mod
    import notion_sync_addon.sync as sync_mod

    notion = FakeNotion()
    notion.pages[P1] = {"edited": OLD, "title": "Hist", "tables": [DB1]}
    notion.pages[P2] = {"edited": OLD, "title": "Philo", "tables": [DB2, DB3]}
    notion.tables[DB1] = {
        "title": "Defs",
        "rows": {
            _rid(i): [f"h{i}", OLD, [_para(f"hist {i}")]] for i in range(3)
        },
    }
    notion.tables[DB2] = {
        "title": "Notions",
        "rows": {
            _rid(10 + i): [f"p{i}", OLD, [_para(f"philo {i}")]]
            for i in range(3)
        },
    }
    notion.tables[DB3] = {
        "title": "Auteurs",
        "rows": {
            _rid(20 + i): [f"a{i}", OLD, [_para(f"author {i}")]]
            for i in range(2)
        },
    }
    monkeypatch.setattr(fetch_mod, "NotionClient", notion)
    monkeypatch.setattr(fetch_mod, "BlockTree", lambda client: client)
    col = collection.Collection(str(tmp_path / "col.anki2"))
    path = str(tmp_path / "user_files" / "cache.json")

    e: Any = SimpleNamespace()
    e.col, e.notion, e.path, e.sync_mod = col, notion, path, sync_mod
    e.cfg = {"notion_token": "t", "notion_pages": [P1, P2]}

    def run(full=False, between=None):
        prep = sync_mod.prepare_sync(col, path, full)
        result = sync_mod.fetch_pages(
            e.cfg,
            prep.known,
            None,
            prep.structure,
            prep.previous,
            prep.sources,
        )
        result.cache_path = path
        if between:
            between()
        stats = sync_mod.apply_sync(col, result)
        e.result = result
        return stats

    def note(row_id):
        nids = col.find_notes(f"Source:{row_id}")
        return col.get_note(nids[0]) if nids else None

    e.sync, e.note = run, note
    e.count = lambda: len(col.find_notes('"note:Notion Sync Card"'))
    yield e
    col.close()


@pytest.fixture
def addon(env, monkeypatch, tmp_path):
    """Run the add-on's ``__init__`` against ``env`` with a fake mw."""
    import notion_sync_addon as addon_mod

    queue = []

    class TaskMan:
        defer = False

        def run_on_main(self, fn):
            queue.append(fn) if self.defer else fn()

    class PM:
        name = "User 1"
        auth = "token"

        def sync_auth(self):
            return self.auth

    class MediaSyncer:
        def is_syncing(self):
            return False

    class FakeMw:
        col = env.col
        pm = PM()
        taskman = TaskMan()
        media_syncer = MediaSyncer()
        sync_clicks = 0

        class addonManager:
            @staticmethod
            def getConfig(name):
                return env.cfg

        class toolbar:
            class web:
                @staticmethod
                def eval(js):
                    pass

        def on_sync_button_clicked(self):
            FakeMw.sync_clicks += 1
            addon_mod._on_ankiweb_sync_will_start()
            addon_mod._on_ankiweb_sync_finished()

    class FakeQueryOp:
        def __init__(self, *, parent, op, success):
            self.op, self.success = op, success
            self.fail: Any = None

        def failure(self, fn):
            self.fail = fn
            return self

        def without_collection(self):
            return self

        def run_in_background(self):
            try:
                value = self.op(FakeMw.col)
            except Exception as exc:  # noqa: BLE001
                self.fail(exc)
                return
            self.success(value)

    shown: List[str] = []
    finished: List[Any] = []
    statuses: List[Any] = []
    monkeypatch.setattr(addon_mod, "mw", FakeMw(), raising=False)
    monkeypatch.setattr(addon_mod, "QueryOp", FakeQueryOp, raising=False)
    monkeypatch.setattr(
        addon_mod, "tooltip", lambda msg, **k: shown.append(msg), raising=False
    )
    monkeypatch.setattr(
        addon_mod,
        "showWarning",
        lambda msg, **k: shown.append(msg),
        raising=False,
    )
    monkeypatch.setattr(addon_mod, "_op_finished", finished.append)
    real_set_status = addon_mod._set_status

    def set_status(text=None):
        statuses.append(text)
        real_set_status(text)

    monkeypatch.setattr(addon_mod, "_set_status", set_status)
    monkeypatch.setattr(addon_mod, "ADDON_DIR", str(tmp_path / "addon"))
    monkeypatch.setitem(addon_mod._state, "busy", False)
    for key in (
        "followup_pending",
        "followup_running",
        "ankiweb_failed",
        "closing",
    ):
        monkeypatch.setitem(addon_mod._state, key, False)

    a: Any = SimpleNamespace()
    a.mod, a.mw, a.shown, a.finished, a.statuses, a.queue = (
        addon_mod,
        FakeMw,
        shown,
        finished,
        statuses,
        queue,
    )
    return a


_QAPP: List[Any] = []  # like Anki's, the test QApplication lives until the end


@pytest.fixture
def qapp():
    """Give the QApplication, made once like Anki's."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PyQt6.QtWidgets")
    if not _QAPP:
        _QAPP.append(
            widgets.QApplication.instance() or widgets.QApplication([])
        )
    return _QAPP[0]
