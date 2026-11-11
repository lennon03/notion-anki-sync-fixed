"""The HTTP API: auth, rate limits, secrets, schedule."""

import base64
import logging
import time

import pytest
from fastapi.testclient import TestClient
from notion_anki_worker import ankiweb, crypto, notion_oauth, runner
from notion_anki_worker.api import (
    SEARCH_MAX_REQUESTS,
    _list_notion_pages,
    create_app,
)
from notion_anki_worker.config import ConfigError, Settings
from notion_anki_worker.pipeline import RunOutcome
from notion_anki_worker.ratelimit import RateLimiter
from notion_anki_worker.redact import ScrubbingFormatter, register, scrub
from notion_anki_worker.store import (
    FULL_SYNC_NEEDED,
    KEY_SCHEDULE,
    LOG_HEAD_LINES,
    MAX_LOG_LINES,
    OK,
    Store,
)

from notion_sync_addon.notion_api import NotionError

SECRET_TOKEN = "secret_" + "n" * 30
HKEY = "host-key-do-not-leak"
PASSWORD = "ankiweb-password-do-not-leak"
SITE = {"Authorization": "Bearer shared-secret-for-tests"}
PAGE = "a" * 32
OAUTH_TOKEN = "oauth-access-token-do-not-leak"
OAUTH_REFRESH = "oauth-refresh-token-do-not-leak"


class FakeOAuth:
    def __init__(self):
        self.works = True
        self.refreshes = 0
        self.fail = False

    def authorize_url(self, state):
        return f"https://notion.example/consent?state={state}"

    def exchange(self, code):
        if self.fail:
            raise notion_oauth.OAuthError()
        assert code == "the-code"
        return notion_oauth.Tokens(OAUTH_TOKEN, OAUTH_REFRESH, "My Space")

    def refresh(self, token):
        assert token == OAUTH_REFRESH
        self.refreshes += 1
        return notion_oauth.Tokens(OAUTH_TOKEN + "2", OAUTH_REFRESH + "2")

    def token_works(self, _token):
        return self.works


def _block(block_id, kind="paragraph", children=False, **extra):
    return {
        "id": block_id,
        "type": kind,
        "has_children": children,
        kind: extra,
    }


class FakeReader:
    """A Notion that serves a small page and counts what is asked."""

    ROW = "1" * 32
    TABLE = "2" * 32
    DATABASE = "3" * 32
    TOGGLE = "4" * 32

    def __init__(self, token, log):
        self.token = token
        self.log = log

    def _ask(self, *call):
        self.log.append((self.token, *call))
        if self.token == "rejected":
            raise NotionError("Notion API error 401 for /pages/x", status=401)

    def get_page(self, page_id):
        self._ask("page", page_id)
        if page_id != PAGE:
            raise NotionError("missing", status=404)
        return {
            "id": "-".join(
                [PAGE[:8], PAGE[8:12], PAGE[12:16], PAGE[16:20], PAGE[20:]]
            ),
            "properties": {
                "t": {"type": "title", "title": [{"plain_text": "Lessons"}]}
            },
        }

    def get_block_children(self, block_id):
        self._ask("children", block_id)
        if block_id == PAGE:
            return [
                _block("5" * 32, "heading_1"),
                _block(self.DATABASE, "child_database", title="Defs"),
                _block(self.TOGGLE, "toggle", children=True),
            ]
        if block_id == self.TOGGLE:
            return [_block("6" * 32, "child_database", title="")]
        if block_id == self.ROW:
            return [_block(self.TOGGLE, "toggle", children=True)]
        return [_block("7" * 32)]

    def get_database(self, database_id):
        self._ask("database", database_id)
        if database_id not in (self.DATABASE, "6" * 32):
            raise NotionError("missing", status=404)
        if database_id == "6" * 32:
            return {"data_sources": [{"id": "8" * 32, "name": "Inner"}]}
        return {"data_sources": [{"id": self.TABLE, "name": "Source name"}]}

    def query_data_source(self, source_id, title_only=False, stop_after=0):
        self._ask("rows", source_id)
        if source_id != self.TABLE:
            raise NotionError("missing", status=404)
        assert title_only
        return [
            {
                "id": "-".join(
                    [
                        self.ROW[:8],
                        self.ROW[8:12],
                        self.ROW[12:16],
                        self.ROW[16:20],
                        self.ROW[20:],
                    ]
                ),
                "properties": {
                    "t": {"type": "title", "title": [{"plain_text": "Row"}]}
                },
            }
        ]


class Harness:
    def __init__(self, settings):
        self.notion_calls = []
        self.logins = []
        self.login_error = None
        self.outcome = RunOutcome(OK, "2 added", {"added": 2})
        self.oauth = FakeOAuth()
        self.requests = []
        self.settings = settings
        self.app = create_app(
            settings,
            login=self._login,
            run_once=self._run,
            start_scheduler=False,
            oauth=self.oauth,
            list_pages=lambda token: self.page_list(token),
            notion_client=lambda token: FakeReader(token, self.notion_calls),
        )
        self.client = TestClient(self.app)
        token = self.client.post(
            "/session",
            json={"password": "owner-password"},
            headers=SITE,
        ).json()["token"]
        self.headers = {**SITE, "X-Session-Token": token}

    def page_list(self, _token):
        pages = [{"id": PAGE, "title": "From Notion", "parent": ""}]
        return {"pages": pages, "truncated": False}

    def _login(self, username, password, _scratch):
        self.logins.append(username)
        if self.login_error:
            raise self.login_error
        return ankiweb.Login(hkey=HKEY, endpoint="https://sync.example/")

    def _run(self, request, _paths, say):
        say(f"log line with {HKEY}")
        self.requests.append(request)
        return self.outcome

    def connect(self):
        response = self.client.post(
            "/connect",
            json={
                "ankiweb": {"username": "me@x.org", "password": PASSWORD},
            },
            headers=self.headers,
        )
        assert response.status_code == 200, response.text
        url = self.client.post(
            "/notion/oauth/start", headers=self.headers
        ).json()["url"]
        finished = self.client.post(
            "/notion/oauth/finish",
            json={"code": "the-code", "state": url.split("state=")[1]},
            headers=self.headers,
        )
        assert finished.status_code == 200, finished.text
        return response

    def add_page(self):
        response = self.client.put(
            "/config", json={"pages": [PAGE]}, headers=self.headers
        )
        assert response.status_code == 200, response.text

    def sync(self):
        response = self.client.post("/sync", headers=self.headers)
        assert response.status_code == 202, response.text
        self.app.state.runner.join(10)
        return response

    def status(self):
        return self.client.get("/status", headers=self.headers).json()


@pytest.fixture
def h(settings, monkeypatch):
    # page titles are fetched from Notion when pages are saved
    monkeypatch.setattr(
        "notion_anki_worker.api._page_titles",
        lambda token, ids, previous: [
            {"id": i, "title": previous.get(i, "Title")} for i in ids
        ],
    )
    return Harness(settings)


def test_health_is_open_and_says_nothing(h):
    assert h.client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/status"),
        ("post", "/connect"),
        ("delete", "/connect"),
        ("put", "/config"),
        ("post", "/sync"),
        ("post", "/backups/x/link"),
    ],
)
def test_routes_need_the_shared_secret_and_a_session(h, method, path):
    call = getattr(h.client, method)
    assert call(path).status_code == 401
    bearer_only = call(path, headers=SITE)
    assert bearer_only.status_code == 401
    assert bearer_only.json()["error"] == "no_session"


def test_session_cant_be_forged_or_opened_with_bad_password(h):
    forged = {**SITE, "X-Session-Token": "abc.def"}
    assert h.client.get("/status", headers=forged).status_code == 401
    bad = h.client.post("/session", json={"password": "nope"}, headers=SITE)
    assert bad.status_code == 401
    assert "token" not in bad.json()


def test_session_route_needs_the_shared_secret(h):
    response = h.client.post("/session", json={"password": "owner-password"})
    assert response.status_code == 401


def test_session_attempts_are_rate_limited_per_client(h):
    headers = {**SITE, "X-Client-IP": "9.9.9.9"}
    for _ in range(10):
        h.client.post("/session", json={"password": "x"}, headers=headers)
    blocked = h.client.post(
        "/session", json={"password": "owner-password"}, headers=headers
    )
    assert blocked.status_code == 429
    other = {**SITE, "X-Client-IP": "1.1.1.1"}
    assert (
        h.client.post(
            "/session", json={"password": "owner-password"}, headers=other
        ).status_code
        == 200
    )


def test_connect_attempts_are_rate_limited(h):
    headers = {**h.headers, "X-Client-IP": "9.9.9.9"}
    body = {"ankiweb": {"username": "a", "password": "b"}}
    h.login_error = ankiweb.LoginRejected()
    for _ in range(5):
        h.client.post("/connect", json=body, headers=headers)
    blocked = h.client.post("/connect", json=body, headers=headers)
    assert blocked.status_code == 429
    assert len(h.logins) == 5


def test_rate_limiter_forgets_after_the_window():
    now = [0.0]
    limiter = RateLimiter(1, 10, window=60, clock=lambda: now[0])
    assert limiter.allow("a") and not limiter.allow("a")
    now[0] = 61
    assert limiter.allow("a")


def test_connect_stores_encrypted_and_never_echoes(h):
    response = h.connect()
    assert response.json() == {"ok": True}
    for leak in (HKEY, PASSWORD, OAUTH_TOKEN):
        assert leak not in response.text
    status = h.status()
    assert status["ankiweb_connected"] and status["notion_connected"]
    assert HKEY not in str(status) and OAUTH_TOKEN not in str(status)

    folder = h.settings.data_dir
    raw = b"".join(p.read_bytes() for p in folder.iterdir() if p.is_file())
    for leak in (HKEY, PASSWORD, OAUTH_TOKEN, "me@x.org"):
        assert leak.encode() not in raw


def test_validation_errors_dont_echo_the_input(h):
    response = h.client.post(
        "/connect",
        json={"ankiweb": {"username": "", "password": PASSWORD}},
        headers=h.headers,
    )
    assert response.status_code == 422
    assert PASSWORD not in response.text
    extra = h.client.post(
        "/connect",
        json={"notion_token": SECRET_TOKEN, "oops": SECRET_TOKEN},
        headers=h.headers,
    )
    assert extra.status_code == 422
    assert SECRET_TOKEN not in extra.text


def test_rejected_login_stores_nothing_and_is_safe_to_show(h):
    h.login_error = ankiweb.LoginRejected()
    response = h.client.post(
        "/connect",
        json={"ankiweb": {"username": "me", "password": PASSWORD}},
        headers=h.headers,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "ankiweb_login_rejected"
    assert PASSWORD not in response.text
    assert not h.status()["ankiweb_connected"]


def test_disconnect_forgets_the_credentials(h):
    h.connect()
    assert h.client.delete("/connect", headers=h.headers).json() == {
        "ok": True
    }
    status = h.status()
    assert not status["ankiweb_connected"]
    assert not status["notion_connected"]


def test_pages_accept_links_and_ids_and_drop_duplicates(h):
    links = [
        f"https://www.notion.so/My-Page-{PAGE}",
        PAGE,
        "",
    ]
    assert (
        h.client.put(
            "/config", json={"pages": links}, headers=h.headers
        ).status_code
        == 200
    )
    assert [p["id"] for p in h.status()["pages"]] == [PAGE]


def test_invalid_page_and_schedule_are_refused(h):
    bad = h.client.put(
        "/config", json={"pages": ["not a page"]}, headers=h.headers
    )
    assert bad.status_code == 422 and bad.json()["error"] == "invalid_page"
    bad = h.client.put(
        "/config", json={"schedule_minutes": 7}, headers=h.headers
    )
    assert bad.status_code == 422


def test_sync_needs_setup_first(h):
    response = h.client.post("/sync", headers=h.headers)
    assert response.status_code == 409
    assert response.json()["error"] == "not_ready"


def test_sync_runs_and_reports_with_secrets_scrubbed(h):
    h.connect()
    h.add_page()
    h.sync()
    status = h.status()
    assert status["latest"]["status"] == OK
    assert status["latest"]["stats"] == {"added": 2}
    assert status["needed_action"] is None
    assert h.requests[0].hkey == HKEY
    assert h.requests[0].endpoint == "https://sync.example/"
    text = str(status)
    assert HKEY not in text and "[redacted]" in text


def test_token_request_failure_says_nothing(monkeypatch):
    def fail(*_a, **_k):
        raise OSError("response with the-client-secret")

    monkeypatch.setattr(notion_oauth.urllib.request, "urlopen", fail)
    client = notion_oauth.NotionOAuth("id", "the-client-secret", "https://x")
    with pytest.raises(notion_oauth.OAuthError) as caught:
        client.exchange("code")
    assert "the-client-secret" not in repr(caught.value)
    assert "state=abc" in client.authorize_url("abc")


def test_second_sync_is_refused_while_one_runs(h, monkeypatch):
    h.connect()
    h.add_page()
    h.app.state.runner._busy.acquire()
    response = h.client.post("/sync", headers=h.headers)
    assert response.status_code == 409
    assert response.json()["error"] == "already_running"
    h.app.state.runner._busy.release()


def test_full_sync_needed_is_surfaced_and_pauses_the_schedule(h):
    h.connect()
    h.add_page()
    h.outcome = RunOutcome(FULL_SYNC_NEEDED, "AnkiWeb needs a full sync")
    h.app.state.store.put(KEY_SCHEDULE, "60")
    h.sync()
    assert h.status()["needed_action"] == "full_sync"
    assert h.status()["next_run_at"] is None


def test_scheduler_starts_a_due_run_once(h):
    h.connect()
    h.add_page()
    h.app.state.store.put(KEY_SCHEDULE, "60")
    scheduler = h.app.state.scheduler
    scheduler.tick()
    h.app.state.runner.join(10)
    assert len(h.requests) == 1
    scheduler.tick()  # not due again
    h.app.state.runner.join(10)
    assert len(h.requests) == 1


def test_scheduler_skips_quietly_when_a_secret_cant_be_read(h, caplog):
    h.connect()
    h.add_page()
    h.app.state.store.put(KEY_SCHEDULE, "60")
    h.app.state.store.put("ankiweb_hkey", "v1:not:valid")
    h.app.state.scheduler.tick()
    assert h.requests == []
    assert not h.app.state.runner.running
    assert not caplog.records


def test_manual_schedule_never_starts_a_run(h):
    h.connect()
    h.add_page()
    h.app.state.scheduler.tick()
    assert h.requests == []
    assert h.status()["next_run_at"] is None


def test_crashed_run_is_marked_interrupted_at_start(h, settings):
    h.app.state.store.start_run("manual")
    again = create_app(settings, start_scheduler=False)
    assert again.state.store.latest_run()["status"] == "interrupted"


def _backup_name(h):
    name = "before-20260101-000000-abc123.colpkg"
    folder = h.settings.data_dir / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(b"pkg")
    return name


def test_backup_link_downloads_and_expires(h):
    name = _backup_name(h)
    assert h.status()["backups"][0]["name"] == name
    link = h.client.post(f"/backups/{name}/link", headers=h.headers)
    assert link.status_code == 200
    got = h.client.get(link.json()["path"])
    assert got.status_code == 200 and got.content == b"pkg"

    bad = link.json()["path"].replace("sig=", "sig=x")
    assert h.client.get(bad).status_code == 403
    expired = f"/backups/{name}/download?exp=1&sig=abc"
    assert h.client.get(expired).status_code == 403


@pytest.mark.parametrize(
    "name", ["..%2Fworker.sqlite3", "evil.colpkg", "worker.sqlite3"]
)
def test_backup_routes_only_serve_backups(h, name):
    response = h.client.post(f"/backups/{name}/link", headers=h.headers)
    assert response.status_code in (404, 422)


def test_a_long_log_keeps_its_start_and_its_end(tmp_path):
    store = Store(tmp_path / "s.sqlite3", crypto.Vault(bytes(32)))
    run_id = store.start_run("manual")
    total = MAX_LOG_LINES + 250
    for n in range(total):
        store.append_log(run_id, f"line {n}")
    log = [line["text"] for line in store.latest_run()["log"]]
    assert len(log) == MAX_LOG_LINES
    assert log[0] == "line 0" and log[-1] == f"line {total - 1}"
    kept = MAX_LOG_LINES - 1
    assert f"{total - kept} lines left out" in log[LOG_HEAD_LINES]
    assert log[LOG_HEAD_LINES + 1] == f"line {total - kept + LOG_HEAD_LINES}"


def test_scrubber_removes_known_and_token_shaped_secrets():
    register("my-known-secret")
    text = scrub("a my-known-secret b ntn_" + "x" * 30)
    assert text == "a [redacted] b [redacted]"


def test_log_formatter_scrubs_tracebacks():
    register("traceback-secret")
    try:
        raise ValueError("boom traceback-secret")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "x", logging.ERROR, "f", 1, "msg", None, sys.exc_info()
        )
    assert "traceback-secret" not in ScrubbingFormatter().format(record)


def test_vault_round_trip_binds_the_name_and_detects_tampering():
    vault = crypto.Vault(bytes(32))
    stored = vault.encrypt("name", "value")
    assert "value" not in stored
    assert vault.decrypt("name", stored) == "value"
    with pytest.raises(crypto.DecryptError):
        vault.decrypt("other", stored)
    with pytest.raises(crypto.DecryptError):
        crypto.Vault(b"\x01" * 32).decrypt("name", stored)


def test_token_signer_expires_and_checks_paths():
    signer = crypto.TokenSigner(bytes(32))
    assert signer.verify(signer.issue("owner")) == "owner"
    assert signer.verify(signer.issue("owner", ttl=-1)) is None
    expires = int(time.time()) + 60
    mac = signer.sign_path("/a", expires)
    assert signer.check_path("/a", expires, mac)
    assert not signer.check_path("/b", expires, mac)


def test_password_hash_verifies():
    stored = crypto.hash_password("pw")
    assert crypto.verify_password("pw", stored)
    assert not crypto.verify_password("no", stored)
    assert not crypto.verify_password("pw", "garbage")


def test_settings_refuse_bad_key_and_hide_secrets():
    env = {
        "WORKER_API_SECRET": "api-secret",
        "ENCRYPTION_KEY": "short",
        "OWNER_PASSWORD_HASH": "x",
    }
    with pytest.raises(ConfigError):
        Settings.from_env(env)
    env["ENCRYPTION_KEY"] = base64.b64encode(bytes(32)).decode()
    assert "api-secret" not in repr(Settings.from_env(env))


def _state(h):
    url = h.client.post("/notion/oauth/start", headers=h.headers).json()["url"]
    return url.split("state=")[1]


def _finish(h, state):
    return h.client.post(
        "/notion/oauth/finish",
        json={"code": "the-code", "state": state},
        headers=h.headers,
    )


def test_notion_sign_in_stores_encrypted_and_never_echoes(h):
    response = _finish(h, _state(h))
    assert response.json() == {"ok": True}
    status = h.status()
    assert status["notion_connected"]
    assert status["notion_oauth_available"]
    assert status["notion_workspace"] == "My Space"
    folder = h.settings.data_dir
    raw = b"".join(p.read_bytes() for p in folder.iterdir() if p.is_file())
    for leak in (OAUTH_TOKEN, OAUTH_REFRESH):
        assert leak.encode() not in raw and leak not in str(status)


def test_notion_sign_in_rejects_a_forged_state(h):
    assert _finish(h, "forged.state").status_code == 400
    assert not h.status()["notion_connected"]


def test_notion_sign_in_needs_a_session(h):
    response = h.client.post("/notion/oauth/start", headers=SITE)
    assert response.status_code == 401


def test_notion_refusal_is_reported_and_stores_nothing(h):
    h.oauth.fail = True
    response = _finish(h, _state(h))
    assert response.json()["error"] == "notion_sign_in_failed"
    assert not h.status()["notion_connected"]


def test_expired_notion_sign_in_is_refreshed_before_a_run(h):
    h.connect()
    _finish(h, _state(h))
    h.add_page()
    h.oauth.works = False
    h.sync()
    assert h.oauth.refreshes == 1
    assert h.requests[0].notion_token == OAUTH_TOKEN + "2"


def test_working_notion_sign_in_is_not_refreshed(h):
    h.connect()
    _finish(h, _state(h))
    h.add_page()
    h.sync()
    assert h.oauth.refreshes == 0


def test_a_pasted_notion_token_is_refused_and_not_stored(h):
    for body in (
        {"notion_token": SECRET_TOKEN},
        {
            "ankiweb": {"username": "me", "password": PASSWORD},
            "notion_token": SECRET_TOKEN,
        },
    ):
        response = h.client.post("/connect", json=body, headers=h.headers)
        assert response.status_code == 422
        assert SECRET_TOKEN not in response.text
    assert h.logins == []
    assert not h.status()["notion_connected"]


def test_without_a_client_id_notion_sign_in_is_off(settings):
    app = create_app(settings, start_scheduler=False)
    assert notion_oauth.from_settings(settings) is None
    assert app.state.runner._oauth is None


def test_page_list_needs_notion_and_returns_pages(h):
    first = h.client.get("/notion/pages", headers=h.headers)
    assert first.status_code == 409
    h.connect()
    pages = h.client.get("/notion/pages", headers=h.headers).json()["pages"]
    assert pages[0]["title"] == "From Notion"


def test_page_list_renews_a_rejected_sign_in_once(h):
    _finish(h, _state(h))
    seen = []

    def listing(token):
        seen.append(token)
        if token != OAUTH_TOKEN + "2":
            raise NotionError("rejected", 401)
        pages = [{"id": PAGE, "title": "After renewal", "parent": ""}]
        return {"pages": pages, "truncated": False}

    h.page_list = listing
    response = h.client.get("/notion/pages", headers=h.headers)
    assert response.json()["pages"][0]["title"] == "After renewal"
    assert h.oauth.refreshes == 1 and len(seen) == 2


def test_sync_options_are_stored_shown_and_used(h):
    h.connect()
    h.add_page()
    assert h.status()["options"]["sync_column"] == "Anki"
    for options in ({"sync_column": "Learn"}, {"page_cards": False}):
        response = h.client.put(
            "/config", json={"options": options}, headers=h.headers
        )
        assert response.status_code == 200, response.text
    shown = h.status()["options"]
    assert shown["sync_column"] == "Learn" and shown["page_cards"] is False
    assert shown["include_empty_cards"] is True
    h.sync()
    assert h.requests[0].options == {
        "sync_column": "Learn",
        "page_cards": False,
    }


@pytest.mark.parametrize(
    "options",
    [{"page_cards": "no"}, {"notion_token": "x"}, {"sync_column": 1}],
)
def test_sync_options_are_validated(h, options):
    response = h.client.put(
        "/config", json={"options": options}, headers=h.headers
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert h.status()["options"]["page_cards"] is True


def test_config_survives_a_token_that_cannot_be_decrypted(h):
    h.connect()
    h.app.state.store.put("notion_token", "v1:not:valid")
    response = h.client.put(
        "/config", json={"pages": [PAGE]}, headers=h.headers
    )
    assert response.status_code == 200


def test_page_list_leaves_out_table_rows(monkeypatch):
    results = [
        {"id": "b" * 32, "parent": {"type": "workspace"}, "properties": {}},
        {"id": "c" * 32, "parent": {"type": "data_source_id"}},
        {"id": "d" * 32, "parent": {"type": "page_id", "page_id": "b" * 32}},
        {"id": "e" * 32, "parent": {"type": "workspace"}, "in_trash": True},
    ]

    class Client:
        def __init__(self, _token):
            pass

        def search(self, _body):
            return {"results": results, "has_more": False}

    monkeypatch.setattr("notion_anki_worker.api.NotionClient", Client)
    found = _list_notion_pages("token")
    pages = found["pages"]
    assert [p["id"] for p in pages] == ["b" * 32, "d" * 32]
    assert pages[1]["parent"] == "b" * 32
    assert found["truncated"] is False


def test_page_list_says_when_it_was_cut(monkeypatch):
    asked = []

    class Client:
        def __init__(self, _token):
            pass

        def search(self, body):
            asked.append(body)
            row = {"id": "c" * 32, "parent": {"type": "data_source_id"}}
            return {"results": [row], "has_more": True, "next_cursor": "x"}

    monkeypatch.setattr("notion_anki_worker.api.NotionClient", Client)
    found = _list_notion_pages("token")
    assert found == {"pages": [], "truncated": True}
    assert len(asked) == SEARCH_MAX_REQUESTS


def test_connecting_ankiweb_forgets_the_old_collection(h):
    h.connect()
    profile = h.settings.data_dir / "profile"
    (profile / "collection.anki2").write_bytes(b"other account")
    h.connect()
    assert not (profile / "collection.anki2").exists()


def test_connections_cant_change_during_a_run(h):
    h.connect()
    h.app.state.runner._busy.acquire()
    try:
        assert (
            h.client.delete("/connect", headers=h.headers).status_code == 409
        )
    finally:
        h.app.state.runner._busy.release()


def test_no_run_can_start_while_connecting_or_forgetting(h, monkeypatch):
    h.connect()
    h.add_page()
    started = []
    forget = runner.pipeline.forget_collection

    def forget_and_try_a_run(paths):
        started.append(h.app.state.runner.start("schedule"))
        forget(paths)

    monkeypatch.setattr(
        runner.pipeline, "forget_collection", forget_and_try_a_run
    )
    h.connect()
    assert h.client.delete("/connect", headers=h.headers).status_code == 200
    assert started == [None, None]
    assert not h.app.state.runner.running


def test_blocking_statuses_cover_both_owner_actions():
    assert FULL_SYNC_NEEDED in runner.BLOCKING_STATUSES


EXPORT_ROUTES = (
    f"/notion/export/page/{PAGE}",
    f"/notion/export/table/{FakeReader.TABLE}/rows",
    f"/notion/export/blocks/{PAGE}",
)


@pytest.mark.parametrize("path", EXPORT_ROUTES)
def test_export_routes_need_the_secret_and_a_session(h, path):
    _finish(h, _state(h))
    assert h.client.get(path).status_code == 401
    assert h.client.get(path, headers=SITE).status_code == 401
    assert h.client.get(path, headers=h.headers).status_code == 200
    assert h.notion_calls


@pytest.mark.parametrize("path", EXPORT_ROUTES)
def test_export_routes_need_notion_connected(h, path):
    response = h.client.get(path, headers=h.headers)
    assert response.status_code == 409
    assert response.json()["error"] == "not_connected"
    assert h.notion_calls == []


def test_export_routes_only_read(h):
    for method in ("post", "put", "delete", "patch"):
        for path in EXPORT_ROUTES:
            sent = getattr(h.client, method)(path, headers=h.headers)
            assert sent.status_code in (404, 405)


def test_export_page_lists_tables_at_any_depth(h):
    _finish(h, _state(h))
    body = h.client.get(
        f"/notion/export/page/{PAGE}", headers=h.headers
    ).json()
    assert body["name"] == "Lessons"
    assert body["tables"] == [
        {"id": FakeReader.TABLE, "name": "Defs"},
        {"id": "8" * 32, "name": "Inner"},
    ]
    assert {c[0] for c in h.notion_calls} == {OAUTH_TOKEN}


def test_export_page_accepts_a_table_id_too(h):
    _finish(h, _state(h))
    body = h.client.get(
        f"/notion/export/page/{FakeReader.TABLE}", headers=h.headers
    ).json()
    assert body["tables"] == [{"id": FakeReader.TABLE, "name": "Notion table"}]


def test_export_rows_and_blocks(h):
    _finish(h, _state(h))
    rows = h.client.get(
        f"/notion/export/table/{FakeReader.TABLE}/rows", headers=h.headers
    ).json()["rows"]
    assert rows == [{"id": FakeReader.ROW, "title": "Row"}]
    blocks = h.client.get(
        f"/notion/export/blocks/{FakeReader.ROW}", headers=h.headers
    ).json()["blocks"]
    assert blocks[0]["children"][0]["id"] == "6" * 32


def test_export_rejects_ids_that_are_not_notion_ids(h):
    _finish(h, _state(h))
    for bad in ("not-an-id", "a" * 31, "..%2F..%2Fusers%2Fme"):
        response = h.client.get(
            f"/notion/export/page/{bad}", headers=h.headers
        )
        assert response.status_code in (404, 422)
        assert h.notion_calls == []


def test_export_unreadable_page_is_a_fixed_message(h):
    _finish(h, _state(h))
    response = h.client.get(
        f"/notion/export/page/{'9' * 32}", headers=h.headers
    )
    assert response.status_code == 404
    assert response.json()["error"] == "not_shared"
    assert "missing" not in response.text


def test_export_renews_a_rejected_sign_in_once(h):
    _finish(h, _state(h))
    h.app.state.store.put_secret("notion_token", "rejected")
    response = h.client.get(f"/notion/export/page/{PAGE}", headers=h.headers)
    assert response.status_code == 200
    assert h.oauth.refreshes == 1
    assert h.notion_calls[0][0] == "rejected"
    assert h.notion_calls[-1][0] == OAUTH_TOKEN + "2"


def test_export_reports_a_rejected_sign_in_without_a_refresh_token(h):
    _finish(h, _state(h))
    h.app.state.store.put_secret("notion_token", "rejected")
    h.app.state.store.delete("notion_refresh_token")
    response = h.client.get(f"/notion/export/page/{PAGE}", headers=h.headers)
    assert response.json()["error"] == "notion_token_rejected"
    assert "Notion API error" not in response.text


def test_export_refuses_more_than_a_request_can_read(h, monkeypatch):
    _finish(h, _state(h))
    monkeypatch.setattr("notion_anki_worker.export_read.MAX_TREE_BLOCKS", 1)
    response = h.client.get(
        f"/notion/export/blocks/{FakeReader.ROW}", headers=h.headers
    )
    assert response.status_code == 413
    assert response.json()["error"] == "too_large"
