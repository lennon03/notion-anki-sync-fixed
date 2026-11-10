"""The HTTP API the site talks to."""

from __future__ import annotations

import hmac
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from notion_anki_worker import (
    ankiweb,
    crypto,
    export_read,
    notion_oauth,
    pipeline,
)
from notion_anki_worker import runner as rn
from notion_anki_worker import store as st
from notion_anki_worker.config import Settings
from notion_anki_worker.ratelimit import RateLimiter
from notion_anki_worker.redact import register
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.exceptions import HTTPException as StarletteHTTPException

from notion_sync_addon.notion_api import NotionClient, NotionError, page_title
from notion_sync_addon.sync import canonical_page_id

log = logging.getLogger(__name__)

T = TypeVar("T")

OWNER = "owner"
SCHEDULES = (0, 15, 30, 60, 180, 360, 720, 1440)
MAX_PAGES = 100
LINK_SECONDS = 5 * 60
OAUTH_STATE = "notion-oauth"
OAUTH_STATE_SECONDS = 10 * 60
SEARCH_PAGE_SIZE = 100
SEARCH_MAX_REQUESTS = 20


class ApiError(Exception):
    """An error with a stable code and a message safe to show."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionBody(_Body):
    """``POST /session``."""

    password: SecretStr = Field(max_length=1024)


class AnkiWebLogin(_Body):
    """The AnkiWeb ID and password, used once."""

    username: str = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024)


class ConnectBody(_Body):
    """``POST /connect``: Notion connects through its own sign-in."""

    ankiweb: AnkiWebLogin


class ConfigBody(_Body):
    """``PUT /config``."""

    pages: Optional[List[str]] = Field(default=None, max_length=MAX_PAGES)
    schedule_minutes: Optional[int] = None


class OAuthFinishBody(_Body):
    """``POST /notion/oauth/finish``: what Notion sent to the redirect."""

    code: SecretStr = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=1, max_length=2048)


class SyncBody(_Body):
    """``POST /sync``."""

    full: bool = False


def _rejected(_request: Request, _exc: Exception) -> JSONResponse:
    # FastAPI's default echoes the offending input, passwords included
    return JSONResponse(
        {"error": "invalid_request", "message": "The request is not valid."},
        status_code=422,
    )


def _api_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse(
        {"error": exc.code, "message": exc.message},
        status_code=exc.status,
    )


def _http_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return JSONResponse(
        {"error": "http_error", "message": "Request failed."},
        status_code=exc.status_code,
    )


def _crashed(_request: Request, exc: Exception) -> JSONResponse:
    log.error("Unhandled API error", exc_info=exc)
    return JSONResponse(
        {"error": "internal", "message": "Something went wrong."},
        status_code=500,
    )


def _client_ip(request: Request) -> str:
    # Set by the site, and only requests with the shared secret get this far
    return request.headers.get("x-client-ip") or "unknown"


def _list_notion_pages(token: str) -> List[Dict[str, str]]:
    """List the pages the token can read, without table rows."""
    client = NotionClient(token)
    pages: List[Dict[str, str]] = []
    cursor = None
    for _ in range(SEARCH_MAX_REQUESTS):
        body: Dict[str, Any] = {
            "filter": {"property": "object", "value": "page"},
            "page_size": SEARCH_PAGE_SIZE,
        }
        if cursor:
            body["start_cursor"] = cursor
        found = client.search(body)
        for page in found.get("results") or []:
            parent = page.get("parent") or {}
            if parent.get("type") in ("database_id", "data_source_id"):
                continue  # a table row, not a page
            if page.get("in_trash") or page.get("archived"):
                continue
            pages.append(
                {
                    "id": canonical_page_id(page["id"]),
                    "title": page_title(page) or "Untitled",
                    "parent": (parent.get("page_id") or "").replace("-", ""),
                }
            )
        cursor = found.get("next_cursor")
        if not found.get("has_more") or not cursor:
            break
    pages.sort(key=lambda p: p["title"].casefold())
    return pages


def _page_titles(
    token: Optional[str], ids: List[str], previous: Dict[str, str]
) -> List[Dict[str, str]]:
    pages: List[Dict[str, str]] = []
    client = NotionClient(token) if token else None
    for page_id in ids:
        title = previous.get(page_id, "")
        if client is not None:
            try:
                title = page_title(client.get_page(page_id)) or title
            except NotionError:
                pass  # not shared yet: the run reports it
        pages.append({"id": page_id, "title": title})
    return pages


def _needed_action(latest: Optional[Dict[str, Any]]) -> Optional[str]:
    if latest is None:
        return None
    return {
        st.FULL_SYNC_NEEDED: "full_sync",
        st.AUTH_FAILED: "reconnect_ankiweb",
    }.get(latest["status"])


def create_app(
    settings: Settings,
    login: Callable[[str, str, Path], ankiweb.Login] = ankiweb.sync_login,
    run_once: Callable[..., pipeline.RunOutcome] = pipeline.run_once,
    start_scheduler: bool = True,
    oauth: Optional[notion_oauth.NotionOAuth] = None,
    list_pages: Callable[[str], List[Dict[str, str]]] = _list_notion_pages,
    notion_client: Callable[[str], NotionClient] = NotionClient,
) -> FastAPI:
    """Build the app."""
    oauth = oauth or notion_oauth.from_settings(settings)
    register(settings.api_secret)
    vault = crypto.Vault(
        crypto.derive_key(settings.encryption_key, "vault-v1")
    )
    signer = crypto.TokenSigner(
        crypto.derive_key(settings.encryption_key, "signing-v1")
    )
    store = st.Store(settings.data_dir / "worker.sqlite3", vault)
    paths = pipeline.Paths(settings.data_dir)
    pipeline.recover(paths)
    store.mark_interrupted()
    runner = rn.Runner(store, paths, run_once, oauth)
    scheduler = rn.Scheduler(store, runner)
    session_limit = RateLimiter(per_ip=10, overall=40)
    connect_limit = RateLimiter(per_ip=5, overall=10)
    if start_scheduler:
        scheduler.start()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_exception_handler(RequestValidationError, _rejected)
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _crashed)
    app.state.runner = runner
    app.state.scheduler = scheduler
    app.state.store = store

    def require_idle() -> None:
        if runner.running:
            raise ApiError(
                409, "already_running", "Wait for the current sync to finish."
            )

    def site(authorization: str = Header(default="")) -> None:
        expected = f"Bearer {settings.api_secret}"
        if not hmac.compare_digest(
            authorization.encode("utf-8"), expected.encode("utf-8")
        ):
            raise ApiError(401, "unauthorized", "Not authorized.")

    def session(
        _site: None = Depends(site),
        x_session_token: str = Header(default=""),
    ) -> str:
        subject = signer.verify(x_session_token)
        if subject != OWNER:
            raise ApiError(401, "no_session", "Sign in first.")
        return subject

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/session")
    def open_session(
        body: SessionBody, request: Request, _site: None = Depends(site)
    ) -> Dict[str, Any]:
        if not session_limit.allow(_client_ip(request)):
            raise ApiError(429, "rate_limited", "Too many attempts. Wait.")
        if not crypto.verify_password(
            body.password.get_secret_value(), settings.owner_password_hash
        ):
            raise ApiError(401, "bad_password", "Wrong password.")
        return {"token": signer.issue(OWNER)}

    @app.get("/status")
    def status(_who: str = Depends(session)) -> Dict[str, Any]:
        latest = store.latest_run()
        return {
            "ankiweb_connected": store.has(st.KEY_HKEY),
            "notion_connected": store.has(st.KEY_NOTION),
            "notion_workspace": store.get(st.KEY_NOTION_WORKSPACE),
            "notion_oauth_available": oauth is not None,
            "pages": store.pages(),
            "schedule_minutes": store.schedule_minutes(),
            "next_run_at": scheduler.next_run_at(),
            "running": runner.running,
            "latest": latest,
            "runs": store.recent_runs(),
            "backups": pipeline.list_backups(paths),
            "needed_action": _needed_action(latest),
        }

    @app.post("/connect")
    def connect(
        body: ConnectBody, request: Request, _who: str = Depends(session)
    ) -> Dict[str, bool]:
        require_idle()
        if not connect_limit.allow(_client_ip(request)):
            raise ApiError(429, "rate_limited", "Too many attempts. Wait.")
        try:
            result = login(
                body.ankiweb.username,
                body.ankiweb.password.get_secret_value(),
                paths.scratch,
            )
        except ankiweb.LoginRejected:
            raise ApiError(
                400,
                "ankiweb_login_rejected",
                "AnkiWeb rejected this ID or password.",
            ) from None
        except ankiweb.LoginUnavailable:
            raise ApiError(
                502, "ankiweb_unavailable", "Could not reach AnkiWeb."
            ) from None
        # the local copy may belong to another account
        pipeline.forget_collection(paths)
        store.put_secret(st.KEY_HKEY, result.hkey)
        if result.endpoint:
            store.put(st.KEY_ENDPOINT, result.endpoint)
        else:
            store.delete(st.KEY_ENDPOINT)
        return {"ok": True}

    @app.delete("/connect")
    def disconnect(_who: str = Depends(session)) -> Dict[str, bool]:
        require_idle()
        store.forget_credentials()
        pipeline.forget_collection(paths)
        return {"ok": True}

    @app.post("/notion/oauth/start")
    def oauth_start(_who: str = Depends(session)) -> Dict[str, str]:
        if oauth is None:
            raise ApiError(
                404, "oauth_not_configured", "Notion sign-in isn't set up."
            )
        state = signer.issue(OAUTH_STATE, ttl=OAUTH_STATE_SECONDS)
        return {"url": oauth.authorize_url(state)}

    @app.post("/notion/oauth/finish")
    def oauth_finish(
        body: OAuthFinishBody, _who: str = Depends(session)
    ) -> Dict[str, bool]:
        if oauth is None:
            raise ApiError(
                404, "oauth_not_configured", "Notion sign-in isn't set up."
            )
        if signer.verify(body.state) != OAUTH_STATE:
            raise ApiError(400, "bad_state", "This sign-in has expired.")
        try:
            tokens = oauth.exchange(body.code.get_secret_value())
        except notion_oauth.OAuthError:
            raise ApiError(
                400, "notion_sign_in_failed", "Notion refused the sign-in."
            ) from None
        store.put_secret(st.KEY_NOTION, tokens.access)
        if tokens.refresh:
            store.put_secret(st.KEY_NOTION_REFRESH, tokens.refresh)
        else:
            store.delete(st.KEY_NOTION_REFRESH)
        store.put(st.KEY_NOTION_WORKSPACE, tokens.workspace)
        return {"ok": True}

    @app.get("/notion/pages")
    def notion_pages(_who: str = Depends(session)) -> Dict[str, Any]:
        try:
            token = store.get_secret(st.KEY_NOTION)
        except crypto.DecryptError:
            token = None
        if not token:
            raise ApiError(409, "not_connected", "Connect Notion first.")
        try:
            return {"pages": list_pages(token)}
        except NotionError as e:
            if e.status == 401:
                raise ApiError(
                    400, "notion_token_rejected", "Notion rejected this token."
                ) from None
            raise ApiError(
                502, "notion_unavailable", "Could not reach Notion."
            ) from None

    refresh_lock = threading.Lock()

    def renewed_token(rejected: str) -> Optional[str]:
        """Swap a rejected Notion sign-in for a fresh one, if possible."""
        with refresh_lock:
            current = store.get_secret(st.KEY_NOTION)
            if current and current != rejected:
                return current  # another request already renewed it
            refresh = store.get_secret(st.KEY_NOTION_REFRESH)
            if oauth is None or not refresh:
                return None
            try:
                tokens = oauth.refresh(refresh)
            except notion_oauth.OAuthError:
                return None
            store.put_secret(st.KEY_NOTION, tokens.access)
            store.put_secret(st.KEY_NOTION_REFRESH, tokens.refresh or refresh)
            return tokens.access

    def notion_read(read: Callable[[NotionClient], T]) -> T:
        """Run a read-only Notion lookup with the stored sign-in.

        Notion's own errors never reach the response: they can quote the
        request, so each one becomes a fixed message.
        """
        try:
            token = store.get_secret(st.KEY_NOTION)
        except crypto.DecryptError:
            token = None
        if not token:
            raise ApiError(409, "not_connected", "Connect Notion first.")
        try:
            try:
                return read(notion_client(token))
            except NotionError as e:
                if e.status != 401:
                    raise
                fresh = renewed_token(token)
                if fresh is None:
                    raise
                return read(notion_client(fresh))
        except export_read.TooLarge:
            raise ApiError(
                413, "too_large", "That is more than one export can read."
            ) from None
        except NotionError as e:
            if e.status == 401:
                raise ApiError(
                    400, "notion_token_rejected", "Notion rejected this token."
                ) from None
            if not e.transient and e.status in (400, 403, 404):
                raise ApiError(
                    404,
                    "not_shared",
                    "Notion can't find this. Share it with the integration.",
                ) from None
            raise ApiError(
                502, "notion_unavailable", "Could not reach Notion."
            ) from None

    def export_id(raw: str) -> str:
        try:
            return export_read.valid_id(raw)
        except NotionError:
            raise ApiError(
                422, "invalid_id", "That is not a Notion page id."
            ) from None

    @app.get("/notion/export/page/{notion_id}")
    def export_page(
        notion_id: str, _who: str = Depends(session)
    ) -> Dict[str, Any]:
        ident = export_id(notion_id)
        return notion_read(lambda client: export_read.describe(client, ident))

    @app.get("/notion/export/table/{notion_id}/rows")
    def export_rows(
        notion_id: str, _who: str = Depends(session)
    ) -> Dict[str, Any]:
        ident = export_id(notion_id)
        rows = notion_read(
            lambda client: export_read.table_rows(client, ident)
        )
        return {"rows": rows}

    @app.get("/notion/export/blocks/{notion_id}")
    def export_blocks(
        notion_id: str, _who: str = Depends(session)
    ) -> Dict[str, Any]:
        ident = export_id(notion_id)
        blocks = notion_read(
            lambda client: export_read.block_tree(client, ident)
        )
        return {"blocks": blocks}

    @app.put("/config")
    def config(
        body: ConfigBody, _who: str = Depends(session)
    ) -> Dict[str, bool]:
        if body.schedule_minutes is not None:
            if body.schedule_minutes not in SCHEDULES:
                raise ApiError(
                    422, "invalid_schedule", "Pick one of the schedules."
                )
            store.put(st.KEY_SCHEDULE, str(body.schedule_minutes))
        if body.pages is not None:
            try:
                ids = list(
                    dict.fromkeys(
                        canonical_page_id(raw)
                        for raw in body.pages
                        if raw.strip()
                    )
                )
            except NotionError:
                raise ApiError(
                    422,
                    "invalid_page",
                    "One of the links is not a Notion page.",
                ) from None
            previous = {p["id"]: p["title"] for p in store.pages()}
            token = store.get_secret(st.KEY_NOTION)
            store.put(
                st.KEY_PAGES, json.dumps(_page_titles(token, ids, previous))
            )
        return {"ok": True}

    @app.post("/sync", status_code=202)
    def sync(
        body: Optional[SyncBody] = None, _who: str = Depends(session)
    ) -> Dict[str, int]:
        try:
            run_id = runner.start("manual", full=bool(body and body.full))
        except rn.NotReady:
            raise ApiError(
                409,
                "not_ready",
                "Connect AnkiWeb and Notion and add a page first.",
            ) from None
        if run_id is None:
            raise ApiError(409, "already_running", "A sync is running.")
        return {"run_id": run_id}

    def backup_path(name: str) -> Path:
        if not pipeline.BACKUP_NAME.fullmatch(name):
            raise ApiError(404, "not_found", "No such backup.")
        path = paths.backups / name
        if not path.is_file():
            raise ApiError(404, "not_found", "No such backup.")
        return path

    @app.post("/backups/{name}/link")
    def backup_link(name: str, _who: str = Depends(session)) -> Dict[str, str]:
        backup_path(name)
        expires = int(time.time()) + LINK_SECONDS
        path = f"/backups/{name}/download"
        signature = signer.sign_path(path, expires)
        return {"path": f"{path}?exp={expires}&sig={signature}"}

    @app.get("/backups/{name}/download")
    def backup_download(name: str, exp: int, sig: str) -> FileResponse:
        path = f"/backups/{name}/download"
        if not signer.check_path(path, exp, sig):
            raise ApiError(403, "bad_link", "This link is no longer valid.")
        return FileResponse(
            backup_path(name),
            media_type="application/octet-stream",
            filename=name,
        )

    return app
