"""Sign in with Notion: the authorization-code flow of a public integration."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from notion_anki_worker.config import Settings

from notion_sync_addon.notion_api import (
    BASE_URL,
    NOTION_VERSION,
    NotionClient,
    NotionError,
)

TIMEOUT_SECONDS = 30


class OAuthError(Exception):
    """Notion didn't give us a token. Carries no response text."""


@dataclass
class Tokens:
    """What a token request returns."""

    access: str = field(repr=False)
    refresh: Optional[str] = field(default=None, repr=False)
    workspace: str = ""


class NotionOAuth:
    """Talks to Notion's OAuth endpoints with the integration's secret."""

    def __init__(
        self, client_id: str, client_secret: str, redirect_uri: str
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    def authorize_url(self, state: str) -> str:
        """Return the consent page to send the owner to."""
        query = urllib.parse.urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "owner": "user",
                "state": state,
            }
        )
        return f"{BASE_URL}/oauth/authorize?{query}"

    def _token(self, body: dict) -> Tokens:
        credentials = f"{self._client_id}:{self._client_secret}".encode()
        request = urllib.request.Request(
            f"{BASE_URL}/oauth/token",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": "Basic "
                + base64.b64encode(credentials).decode("ascii"),
                "Content-Type": "application/json",
                "Notion-Version": NOTION_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=TIMEOUT_SECONDS
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            raise OAuthError() from None
        access = data.get("access_token")
        if not access:
            raise OAuthError()
        return Tokens(
            access=access,
            refresh=data.get("refresh_token"),
            workspace=data.get("workspace_name") or "",
        )

    def exchange(self, code: str) -> Tokens:
        """Trade the code from the redirect for tokens."""
        return self._token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
            }
        )

    def refresh(self, refresh_token: str) -> Tokens:
        """Get a new pair of tokens."""
        return self._token(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    def token_works(self, token: str) -> bool:
        """Say False only when Notion itself rejects the token."""
        try:
            NotionClient(token)._request("GET", "/users/me")
        except NotionError as e:
            return e.status != 401
        return True


def from_settings(settings: Settings) -> Optional[NotionOAuth]:
    """Build the client, or None when the integration isn't configured."""
    if (
        settings.notion_client_id
        and settings.notion_client_secret
        and settings.notion_redirect_uri
    ):
        return NotionOAuth(
            settings.notion_client_id,
            settings.notion_client_secret,
            settings.notion_redirect_uri,
        )
    return None
