# -*- coding: utf-8 -*-
"""The row cache: when a row counts as unchanged, and the cache file."""

import os
import time

from notion_sync_addon.sync import RowState, is_unchanged
from tests.helpers import DB2, EDITED, EDITED_TS


def test_unchanged_requires_same_edit_time_full_minute_and_skew_margin():
    now = EDITED_TS + 3600
    fresh = RowState(EDITED, EDITED_TS + 600)
    assert is_unchanged(fresh, EDITED, "k", now)
    # fetched during the edit minute: a later edit in that minute is invisible
    assert not is_unchanged(RowState(EDITED, EDITED_TS + 30), EDITED, "k", now)
    # this computer's clock may run a few minutes ahead of Notion's
    assert not is_unchanged(
        RowState(EDITED, EDITED_TS + 120), EDITED, "k", now
    )
    assert not is_unchanged(fresh, "2026-11-04T11:45:00.000Z", "k", now)
    assert not is_unchanged(None, EDITED, "k", now)
    assert not is_unchanged(fresh, "", "k", now)
    # content that can change without bumping last_edited_time: an hour
    volatile = RowState(EDITED, EDITED_TS + 600, volatile=True)
    assert is_unchanged(volatile, EDITED, "k", EDITED_TS + 600 + 3599)
    assert not is_unchanged(volatile, EDITED, "k", EDITED_TS + 600 + 3600)
    # fetched "in the future": the clock was wrong then
    assert not is_unchanged(RowState(EDITED, now + 3600), EDITED, "k", now)


def test_cached_rows_expire_after_7_to_14_days():
    from notion_sync_addon.sync import (
        AGE_SPREAD_SECONDS,
        MAX_AGE_SECONDS,
        max_age,
    )

    ages = [max_age(f"row{i}") for i in range(200)]
    assert all(
        MAX_AGE_SECONDS <= a <= MAX_AGE_SECONDS + AGE_SPREAD_SECONDS
        for a in ages
    )
    assert (
        max(ages) - min(ages) > AGE_SPREAD_SECONDS / 2
    )  # spread, not one day
    state = RowState(EDITED, EDITED_TS + 600)
    assert is_unchanged(
        state, EDITED, "row1", EDITED_TS + 600 + max_age("row1") - 1
    )
    assert not is_unchanged(
        state, EDITED, "row1", EDITED_TS + 600 + max_age("row1") + 1
    )


def test_cache_path_is_per_profile_and_safe():
    from notion_sync_addon.sync import cache_path

    a = cache_path("/addon", "User 1")
    b = cache_path("/addon", "User_1")
    c = cache_path("/addon", "../../evil\\name")
    assert a != b
    assert os.path.dirname(a) == os.path.join("/addon", "user_files")
    assert os.path.dirname(c) == os.path.join("/addon", "user_files")
    assert ".." not in os.path.basename(c) and "\\" not in os.path.basename(c)


def test_cache_file_roundtrip_corrupt_and_unchanged(tmp_path):
    from notion_sync_addon.sync import Cache, load_cache, save_cache

    path = str(tmp_path / "user_files" / "cache.json")
    assert load_cache(path).rows == {}  # missing
    cache = Cache(
        rows={"r": RowState(EDITED, EDITED_TS, "fp", "p", "d", True)},
        pages={"p": [EDITED, EDITED_TS, [["d", "Defs"]]]},
        dbs={"d": "ds"},
    )
    assert save_cache(path, cache)
    loaded = load_cache(path)
    assert (
        loaded.rows == cache.rows
        and loaded.pages == cache.pages
        and loaded.dbs == cache.dbs
    )
    assert not save_cache(path, loaded)  # unchanged: not rewritten
    assert os.listdir(tmp_path / "user_files") == [
        "cache.json"
    ]  # no temp file left
    for junk in (
        "{not json",
        "[]",
        '{"v": -1, "rows": {}}',
        '{"v": 2, "rows": {"r": [1]}, "pages": {"p": [1, "x", 3]}}',
    ):
        with open(path, "w", encoding="utf-8") as f:
            f.write(junk)
        bad = load_cache(path)
        assert bad.rows == {} and bad.pages == {}


def test_cache_checked_map_roundtrips_and_is_optional(tmp_path):
    from notion_sync_addon.sync import Cache, load_cache, save_cache

    path = str(tmp_path / "c.json")
    save_cache(path, Cache())
    assert "checked" not in open(path, encoding="utf-8").read()
    save_cache(path, Cache(checked={"a" * 32: 12.5}))
    assert load_cache(path).checked == {"a" * 32: 12.5}
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"v": 3, "checked": {"x": "bad", "y": 1}}')
    assert load_cache(path).checked == {"y": 1.0}


def test_cache_of_another_render_version_keeps_row_origins(tmp_path):
    """N1: the RENDER_VERSION bump re-renders every row but keeps origins,
    so removal stays scoped during that first sync."""
    import json

    from notion_sync_addon.sync import RENDER_VERSION, load_cache

    path = tmp_path / "cache.json"
    path.write_text(
        json.dumps(
            {
                "v": RENDER_VERSION - 1,
                "rows": {"r": [EDITED, EDITED_TS, "fp", "p", "d", 0]},
                "pages": {"p": [EDITED, EDITED_TS, [["d", "Defs"]]]},
                "dbs": {"d": "ds"},
            }
        )
    )
    cache = load_cache(str(path))
    assert cache.rows["r"] == RowState("", 0.0, "", "p", "d")  # never trusted
    assert cache.pages and cache.dbs == {"d": "ds"} and cache.raw == ""


def test_old_cache_entries_and_round_trip():
    old_false = RowState.from_json(["e", 1.0, "fp", "p", "d", False])
    old_true = RowState.from_json(["e", 1.0, "fp", "p", "d", True])
    assert old_false is not None and old_false.volatile == 0
    assert old_true is not None and old_true.volatile == 1
    for level in (0, 1):
        state = RowState("e", 1.5, "fp", "p", "d", level)
        assert RowState.from_json(state.to_json()) == state


def test_legacy_config_cache_is_removed(env):
    env.col.set_config("notionSync.rowState", {"v": 1, "rows": {}})
    env.sync()
    assert env.col.get_config("notionSync.rowState", None) is None


def test_failures_are_kept_in_the_cache_until_a_clean_sync(env):
    """N4c: the settings dialog reads the last sync's problems."""
    env.sync()
    env.notion.fail = {("query", DB2)}
    env.sync()
    problems = env.sync_mod.load_problems(env.path)
    assert (
        len(problems) == 1
        and "Notions" in problems[0]
        and "Philo" in problems[0]
    )
    env.notion.fail = set()
    env.sync()
    assert env.sync_mod.load_problems(env.path) == []


def test_cache_write_failure_is_only_a_warning(env, monkeypatch):
    """N5: os.replace denied (antivirus, OneDrive): notes written, warned."""
    calls = []

    def deny(src, dst):
        calls.append(dst)
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(os, "replace", deny)
    stats = env.sync()
    assert stats["added"] == 8 and env.count() == 8 and len(calls) == 2
    assert any("cache" in w for w in env.result.warnings)
    assert not [
        f for f in os.listdir(os.path.dirname(env.path)) if f.endswith(".tmp")
    ]


def test_cache_write_is_retried_once(env, monkeypatch):
    real = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) == 1:
            raise PermissionError(13, "Access is denied")
        return real(src, dst)

    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(os, "replace", flaky)
    env.sync()
    assert len(calls) == 2 and not env.result.warnings
    assert os.path.exists(env.path)
