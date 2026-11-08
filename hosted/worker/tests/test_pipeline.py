"""A run against a real collection, with AnkiWeb and Notion faked."""

import json
from datetime import datetime, timedelta, timezone

from anki.collection import SyncOutput
from anki.errors import SyncError, SyncErrorKind
from notion_anki_worker import pipeline
from notion_anki_worker.pipeline import MEDIA_DB, MEDIA_FOLDER, run_once
from notion_anki_worker.store import (
    AUTH_FAILED,
    FAILED,
    FULL_SYNC_NEEDED,
    OK,
    WARNINGS,
)

from notion_sync_addon.notion_api import NotionError
from tests.test_htmlify_and_sync import DB1, OLD, P1, P2, _para, _rid


def _say(_line):
    pass


def _recent(offset=0):
    """An edit time newer than any earlier run."""
    moment = datetime.now(timezone.utc) + timedelta(minutes=10 + offset)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def test_first_run_adds_cards_and_pushes_to_ankiweb(
    web, notion, paths, request_
):
    outcome = run_once(request_, paths, _say)
    assert outcome.status == OK, outcome.message
    assert outcome.stats["added"] == 2
    assert web.notes() == ["my own card", "front 0", "front 1"]
    assert not web.uploads
    assert [c for c in web.calls if c[0] == "full"] == [("full", False)]
    assert not list(paths.scratch.iterdir())


def test_nothing_changed_leaves_ankiweb_alone(web, notion, paths, request_):
    run_once(request_, paths, _say)
    web.calls.clear()
    notion.downloads.clear()
    notion.calls.clear()
    notion.searches = 0
    backups = pipeline.list_backups(paths)

    outcome = run_once(request_, paths, _say)

    assert outcome.status == OK
    assert outcome.backup is None
    assert notion.downloads == []  # the cache held between runs
    assert [c[0] for c in web.calls] == ["sync"]  # a pull: no download, push
    assert outcome.message == "Nothing to sync"
    assert notion.searches == 1 and notion.calls == []  # Notion: one question
    assert pipeline.list_backups(paths) == backups


def test_edited_row_reaches_ankiweb(web, notion, paths, request_):
    run_once(request_, paths, _say)
    rows = notion.tables[DB1]["rows"]
    rows[_rid(0)] = ["edited", _recent(), [_para("new")]]
    notion.pages[P1]["edited"] = _recent()

    outcome = run_once(request_, paths, _say)

    assert outcome.stats["updated"] == 1
    assert "edited" in web.notes()


def test_backup_is_the_state_before_the_run(web, notion, paths, request_):
    outcome = run_once(request_, paths, _say)
    assert outcome.backup
    assert (paths.backups / outcome.backup).stat().st_size > 0


def test_only_the_newest_backups_are_kept(web, notion, paths, request_):
    for i in range(pipeline.BACKUP_KEEP + 2):
        notion.tables[DB1]["rows"][_rid(0)] = [
            f"edit {i}",
            _recent(i),
            [_para("x")],
        ]
        notion.pages[P1]["edited"] = _recent(i)
        run_once(request_, paths, _say)
    assert len(pipeline.list_backups(paths)) == pipeline.BACKUP_KEEP


def test_full_sync_answer_stops_before_touching_anything(
    web, notion, paths, request_
):
    web.first_answer = SyncOutput.FULL_SYNC
    before = web.notes()

    outcome = run_once(request_, paths, _say)

    assert outcome.status == FULL_SYNC_NEEDED
    assert [c[0] for c in web.calls] == ["sync"]
    assert not web.uploads and web.notes() == before
    assert notion.calls == []


def test_full_upload_answer_is_refused_too(web, notion, paths, request_):
    web.first_answer = SyncOutput.FULL_UPLOAD
    outcome = run_once(request_, paths, _say)
    assert outcome.status == FULL_SYNC_NEEDED
    assert not web.uploads


def test_full_sync_answer_at_push_changes_nothing(
    web, notion, paths, request_
):
    web.push_answer = SyncOutput.FULL_SYNC
    before = web.notes()

    outcome = run_once(request_, paths, _say)

    assert outcome.status == FULL_SYNC_NEEDED
    assert web.notes() == before and not web.uploads
    assert not paths.cache.exists()  # the cache only moves on a good push
    assert pipeline.list_backups(paths) == []


def test_rejected_host_key_asks_to_reconnect(web, notion, paths, request_):
    web.error = SyncError("no", None, None, None, SyncErrorKind.AUTH)
    assert run_once(request_, paths, _say).status == AUTH_FAILED


def test_ankiweb_error_fails_the_run(web, notion, paths, request_):
    web.error = SyncError("busy", None, None, None, SyncErrorKind.OTHER)
    outcome = run_once(request_, paths, _say)
    assert outcome.status == FAILED
    assert "busy" in outcome.message
    assert not list(paths.scratch.iterdir())


def test_notion_failure_keeps_cards_and_warns(web, notion, paths, request_):
    run_once(request_, paths, _say)
    notion.fail.add(("query", DB1))
    notion.pages[P1]["edited"] = _recent()
    before = web.notes()

    outcome = run_once(request_, paths, _say)

    assert outcome.status == WARNINGS
    assert outcome.warnings
    assert web.notes() == before


def test_endpoint_change_is_used_and_returned(web, notion, paths, request_):
    web.new_endpoint = "https://sync9.example/"
    outcome = run_once(request_, paths, _say)
    assert outcome.endpoint == "https://sync9.example/"
    assert web.calls[-1][2] == "https://sync9.example/"


def _with_image(notion):
    block = {
        "type": "image",
        "id": "img1",
        "has_children": False,
        "image": {
            "type": "file",
            "file": {"url": "https://files.notion.so/x/a.png?sig=1"},
            "caption": [],
        },
    }
    notion.tables[DB1]["rows"][_rid(0)] = ["front 0", OLD, [block]]


def test_new_media_is_synced_and_the_store_survives(
    web, notion, paths, request_
):
    _with_image(notion)
    outcome = run_once(request_, paths, _say)
    assert outcome.status == OK, outcome.warnings
    assert outcome.media_pending is False
    assert web.media_syncs == 1
    assert list((paths.profile / MEDIA_FOLDER).iterdir())
    assert not (paths.profile / pipeline.MEDIA_PENDING).exists()


def test_no_media_sync_without_new_files(web, notion, paths, request_):
    run_once(request_, paths, _say)
    assert web.media_syncs == 0


def test_failed_media_sync_is_retried_next_run(web, notion, paths, request_):
    _with_image(notion)
    web.media_error = RuntimeError("media down")
    outcome = run_once(request_, paths, _say)
    assert outcome.media_pending is True
    assert any("Media" in w for w in outcome.warnings)

    assert (paths.profile / pipeline.MEDIA_PENDING).exists()

    web.media_error = None
    outcome = run_once(request_, paths, _say)
    assert outcome.media_pending is False
    assert web.media_syncs == 2
    assert not (paths.profile / pipeline.MEDIA_PENDING).exists()


def test_files_staged_by_a_failed_run_are_not_forgotten(
    web, notion, paths, request_
):
    _with_image(notion)
    web.push_answer = SyncOutput.FULL_SYNC
    assert run_once(request_, paths, _say).status == FULL_SYNC_NEEDED
    assert list((paths.profile / MEDIA_FOLDER).iterdir())

    web.push_answer = SyncOutput.NO_CHANGES
    outcome = run_once(request_, paths, _say)
    assert outcome.status == OK
    assert web.media_syncs == 1


def test_media_database_without_its_files_is_discarded(paths):
    (paths.profile / MEDIA_DB).write_bytes(b"x")
    col, _existed = pipeline._open_collection(paths)
    col.close()
    db = paths.profile / MEDIA_DB
    assert not db.exists() or db.read_bytes() != b"x"


def test_start_up_moves_old_media_and_clears_scratch(paths):
    legacy = paths.root / "media"
    (legacy / MEDIA_FOLDER).mkdir(parents=True)
    (legacy / MEDIA_FOLDER / "a.png").write_bytes(b"x")
    (legacy / MEDIA_DB).write_bytes(b"db")
    leftover = paths.scratch / "run-old"
    leftover.mkdir()

    pipeline.recover(paths)

    assert (paths.profile / MEDIA_FOLDER / "a.png").exists()
    assert (paths.profile / MEDIA_DB).exists()
    assert not leftover.exists() and not legacy.exists()


def test_the_collection_is_downloaded_once(web, notion, paths, request_):
    run_once(request_, paths, _say)
    run_once(request_, paths, _say)
    assert web.downloads == 1


def test_changed_page_list_reads_notion(web, notion, paths, request_):
    run_once(request_, paths, _say)
    notion.calls.clear()
    notion.pages[P2] = {"edited": OLD, "title": "New", "tables": []}
    request_.page_ids = [P1, P2]
    run_once(request_, paths, _say)
    assert ("page", P2) in notion.calls


def _rewrite_state(paths, **changes):
    state = json.loads((paths.profile / pipeline.RUN_STATE).read_text())
    state.update(changes)
    (paths.profile / pipeline.RUN_STATE).write_text(json.dumps(state))


def test_a_changed_note_reads_notion(web, notion, paths, request_):
    run_once(request_, paths, _say)
    notion.calls.clear()
    _rewrite_state(paths, notes=[0, 0])
    run_once(request_, paths, _say)
    assert notion.calls


def test_notion_is_read_in_full_every_so_often(web, notion, paths, request_):
    run_once(request_, paths, _say)
    notion.calls.clear()
    _rewrite_state(paths, full_at=0)
    run_once(request_, paths, _say)
    assert notion.calls


def test_full_resync_ignores_the_shortcut(web, notion, paths, request_):
    run_once(request_, paths, _say)
    notion.downloads.clear()
    request_.full = True
    run_once(request_, paths, _say)
    assert notion.downloads


def test_recent_edit_reads_notion(web, notion, paths, request_):
    run_once(request_, paths, _say)
    notion.calls.clear()
    notion.pages[P1]["edited"] = _recent()
    run_once(request_, paths, _say)
    assert notion.calls


def test_notion_search_failure_falls_back_to_reading(
    web, notion, paths, request_, monkeypatch
):
    run_once(request_, paths, _say)
    notion.calls.clear()

    def down(*_a, **_k):
        raise NotionError("down", 503, transient=True)

    monkeypatch.setattr(notion, "_request", down)
    outcome = run_once(request_, paths, _say)
    assert outcome.status == OK and notion.calls


def test_a_failed_push_starts_over_from_ankiweb(web, notion, paths, request_):
    web.push_answer = SyncOutput.FULL_SYNC
    run_once(request_, paths, _say)
    assert (paths.profile / pipeline.DIRTY).exists()

    web.push_answer = SyncOutput.NO_CHANGES
    outcome = run_once(request_, paths, _say)

    assert outcome.status == OK
    assert web.downloads == 2
    assert not (paths.profile / pipeline.DIRTY).exists()


def test_replaced_ankiweb_collection_is_downloaded_again(
    web, notion, paths, request_
):
    run_once(request_, paths, _say)
    web.pull_answer = SyncOutput.FULL_SYNC
    outcome = run_once(request_, paths, _say)
    assert outcome.status == OK
    assert web.downloads == 2 and not web.uploads


def test_full_sync_after_starting_over_stops(web, notion, paths, request_):
    run_once(request_, paths, _say)
    web.pull_answer = SyncOutput.FULL_SYNC
    web.first_answer = SyncOutput.FULL_SYNC
    outcome = run_once(request_, paths, _say)
    assert outcome.status == FULL_SYNC_NEEDED and not web.uploads


def test_turning_a_page_off_keeps_its_cards(web, notion, paths, request_):
    run_once(request_, paths, _say)
    before = web.notes()
    notion.pages[P2] = {"edited": OLD, "title": "Other", "tables": []}
    request_.page_ids = [P2]

    outcome = run_once(request_, paths, _say)

    assert outcome.stats["removed"] == 0
    assert web.notes() == before


def test_a_row_deleted_in_a_selected_page_is_removed(
    web, notion, paths, request_
):
    run_once(request_, paths, _say)
    del notion.tables[DB1]["rows"][_rid(0)]
    notion.pages[P1]["edited"] = _recent()

    outcome = run_once(request_, paths, _say)

    assert outcome.stats["removed"] == 1
    assert "front 0" not in web.notes()


def test_cards_of_unknown_origin_are_kept(web, notion, paths, request_):
    run_once(request_, paths, _say)
    paths.cache.unlink()
    (paths.profile / pipeline.RUN_STATE).unlink()
    del notion.tables[DB1]["rows"][_rid(0)]

    outcome = run_once(request_, paths, _say)

    assert outcome.stats["removed"] == 0
    assert "front 0" in web.notes()


def test_addon_config_keeps_defaults_and_adds_pages():
    cfg = pipeline.addon_config("tok", ["a" * 32])
    assert cfg["notion_token"] == "tok"
    assert cfg["notion_pages"] == [{"page_id": "a" * 32}]
    assert cfg["sync_column"] == "Anki"
    json.dumps(cfg)


def test_broken_notion_never_raises_out_of_a_run(
    web, notion, paths, request_, monkeypatch
):
    def boom(*_a, **_k):
        raise NotionError("token bad", 401)

    monkeypatch.setattr(pipeline.addon_sync, "fetch_pages", boom)
    outcome = run_once(request_, paths, _say)
    assert outcome.status == FAILED
    assert "rejected" in outcome.message
    assert not list(paths.scratch.iterdir())
