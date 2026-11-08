"""The HTTP API: auth, rate limits, secrets, schedule."""

import base64
import logging
import time

import pytest
from fastapi.testclient import TestClient
from notion_anki_worker import ankiweb, crypto, runner
from notion_anki_worker.api import create_app
from notion_anki_worker.config import ConfigError, Settings
from notion_anki_worker.pipeline import RunOutcome
from notion_anki_worker.ratelimit import RateLimiter
from notion_anki_worker.redact import ScrubbingFormatter, register, scrub
from notion_anki_worker.store import FULL_SYNC_NEEDED, KEY_SCHEDULE, OK

from notion_sync_addon.notion_api import NotionError

SECRET_TOKEN = "secret_" + "n" * 30
HKEY = "host-key-do-not-leak"
PASSWORD = "ankiweb-password-do-not-leak"
SITE = {"Authorization": "Bearer shared-secret-for-tests"}
PAGE = "a" * 32


class Harness:
    def __init__(self, settings):
        self.logins = []
        self.notion_error = None
        self.login_error = None
        self.outcome = RunOutcome(OK, "2 added", {"added": 2})
        self.requests = []
        self.settings = settings
        self.app = create_app(
            settings,
            login=self._login,
            check_notion=self._check,
            run_once=self._run,
            start_scheduler=False,
        )
        self.client = TestClient(self.app)
        token = self.client.post(
            "/session",
            json={"password": "owner-password"},
            headers=SITE,
        ).json()["token"]
        self.headers = {**SITE, "X-Session-Token": token}

    def _login(self, username, password, _scratch):
        self.logins.append(username)
        if self.login_error:
            raise self.login_error
        return ankiweb.Login(hkey=HKEY, endpoint="https://sync.example/")

    def _check(self, _token):
        if self.notion_error:
            raise self.notion_error

    def _run(self, request, _paths, say):
        say(f"log line with {HKEY}")
        self.requests.append(request)
        return self.outcome

    def connect(self):
        response = self.client.post(
            "/connect",
            json={
                "ankiweb": {"username": "me@x.org", "password": PASSWORD},
                "notion_token": SECRET_TOKEN,
            },
            headers=self.headers,
        )
        assert response.status_code == 200, response.text
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
    for leak in (HKEY, PASSWORD, SECRET_TOKEN):
        assert leak not in response.text
    status = h.status()
    assert status["ankiweb_connected"] and status["notion_connected"]
    assert HKEY not in str(status) and SECRET_TOKEN not in str(status)

    folder = h.settings.data_dir
    raw = b"".join(p.read_bytes() for p in folder.iterdir() if p.is_file())
    for leak in (HKEY, PASSWORD, SECRET_TOKEN, "me@x.org"):
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


def test_rejected_notion_token_is_not_stored_and_skips_ankiweb(h):
    h.notion_error = NotionError("bad", 401)
    response = h.client.post(
        "/connect",
        json={
            "ankiweb": {"username": "me", "password": PASSWORD},
            "notion_token": SECRET_TOKEN,
        },
        headers=h.headers,
    )
    assert response.status_code == 400
    assert h.logins == []
    assert not h.status()["notion_connected"]


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


def test_blocking_statuses_cover_both_owner_actions():
    assert FULL_SYNC_NEEDED in runner.BLOCKING_STATUSES
