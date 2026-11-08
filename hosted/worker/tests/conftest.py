"""Shared fixtures: a fake AnkiWeb and Notion around a real collection."""

import base64
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "hosted" / "worker")]

from anki.collection import Collection, SyncOutput  # noqa: E402
from notion_anki_worker import crypto  # noqa: E402
from notion_anki_worker.config import Settings  # noqa: E402
from notion_anki_worker.pipeline import Paths, RunRequest  # noqa: E402

from notion_sync_addon import sync as addon_sync  # noqa: E402
from tests.test_htmlify_and_sync import (  # noqa: E402
    DB1,
    OLD,
    P1,
    FakeNotion,
    _para,
    _rid,
)


class SearchingNotion(FakeNotion):
    """Answers the "anything edited lately?" search too."""

    def __init__(self):
        super().__init__()
        self.searches = 0

    def _request(self, method, path, data=None):
        assert (method, path) == ("POST", "/search")
        self.searches += 1
        edited = [p["edited"] for p in self.pages.values()]
        for table in self.tables.values():
            edited += [row[1] for row in table["rows"].values()]
        return {"results": [{"last_edited_time": max(edited)}]}


class FakeAnkiWeb:
    """Stands in for AnkiWeb, holding a real collection file."""

    def __init__(self, folder: Path) -> None:
        self.path = folder / "server.anki2"
        self.calls = []
        self.uploads = 0
        self.first_answer = SyncOutput.FULL_DOWNLOAD
        self.pull_answer = SyncOutput.NO_CHANGES
        self.synced_mod = None
        self.downloads = 0
        self.push_answer = SyncOutput.NO_CHANGES
        self.error = None
        self.media_error = None
        self.media_syncs = 0
        self.new_endpoint = ""
        col = Collection(str(self.path))
        note = col.new_note(col.models.by_name("Basic"))
        note["Front"] = "my own card"
        col.add_note(note, 1)
        col.close()

    def _empty(self, col) -> bool:
        return col.note_count() == 0

    def sync_collection(self, col, auth, sync_media):
        assert sync_media is False
        self.calls.append(("sync", auth.hkey, auth.endpoint))
        if self.error is not None:
            raise self.error
        if self._empty(col):
            required = self.first_answer
        elif col.mod == self.synced_mod:  # nothing local: a pull
            required = self.pull_answer
        else:  # the normal sync that sends the local changes
            required = self.push_answer
            if required == SyncOutput.NO_CHANGES:
                col.db.execute("pragma wal_checkpoint(truncate)")
                shutil.copyfile(col.path, self.path)
                self.synced_mod = col.mod
        return SyncOutput(required=required, new_endpoint=self.new_endpoint)

    def full(self, col, *, auth, server_usn, upload):
        self.calls.append(("full", upload))
        if upload:  # the worker must never get here
            self.uploads += 1
            raise AssertionError("full upload attempted")
        col._backend.close_collection(downgrade_to_schema11=False)
        shutil.copyfile(self.path, col.path)
        base = Path(col.path)
        col._backend.open_collection(
            collection_path=col.path,
            media_folder_path=str(base.with_suffix(".media")),
            media_db_path=str(base.with_suffix(".media.db2")),
        )
        self.downloads += 1
        self.synced_mod = col._backend.db_query(
            "select mod from col", [], False
        )[0][0]

    def sync_media(self, col, auth):
        self.media_syncs += 1

    def media_sync_status(self, col):
        if self.media_error is not None:
            raise self.media_error
        return SimpleNamespace(active=False)

    def notes(self):
        col = Collection(str(self.path))
        try:
            return [
                f.split("\x1f")[0]
                for f in col.db.list("select flds from notes order by id")
            ]
        finally:
            col.close()


@pytest.fixture
def web(tmp_path, monkeypatch):
    """AnkiWeb, with the anki library's network calls pointed at it."""
    folder = tmp_path / "ankiweb"
    folder.mkdir()
    fake = FakeAnkiWeb(folder)
    # plain functions: Collection passes itself as the first argument
    for name, method in (
        ("sync_collection", fake.sync_collection),
        ("full_upload_or_download", fake.full),
        ("sync_media", fake.sync_media),
        ("media_sync_status", fake.media_sync_status),
    ):
        monkeypatch.setattr(
            Collection,
            name,
            lambda col, *a, _m=method, **k: _m(col, *a, **k),
        )
    return fake


@pytest.fixture
def notion(monkeypatch):
    """Notion with one page, one table, two rows."""
    fake = SearchingNotion()
    fake.pages[P1] = {"edited": OLD, "title": "Hist", "tables": [DB1]}
    fake.tables[DB1] = {
        "title": "Defs",
        "rows": {
            _rid(i): [f"front {i}", OLD, [_para(f"back {i}")]]
            for i in range(2)
        },
    }
    monkeypatch.setattr(addon_sync, "NotionClient", fake)
    monkeypatch.setattr(addon_sync, "BlockTree", lambda client: client)
    return fake


@pytest.fixture
def paths(tmp_path):
    """Volume folders."""
    result = Paths(tmp_path / "data")
    result.ensure()
    return result


@pytest.fixture
def request_():
    """A run request."""
    return RunRequest(
        hkey="host-key-abc",
        endpoint=None,
        notion_token="secret_" + "t" * 30,
        page_ids=[P1],
    )


@pytest.fixture
def settings(tmp_path):
    """Worker settings for tests."""
    return Settings(
        data_dir=tmp_path / "data",
        api_secret="shared-secret-for-tests",
        encryption_key=base64.b64decode(base64.b64encode(os.urandom(32))),
        owner_password_hash=crypto.hash_password("owner-password"),
    )
