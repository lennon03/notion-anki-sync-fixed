"""AnkiWeb login: the password goes in once and only a host key comes out."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from anki.collection import Collection
from anki.errors import NetworkError, SyncError, SyncErrorKind


class LoginRejected(Exception):
    """AnkiWeb said the ID or password is wrong."""


class LoginUnavailable(Exception):
    """AnkiWeb could not be asked (network, server error)."""


@dataclass
class Login:
    """What a successful login leaves behind."""

    hkey: str = field(repr=False)
    endpoint: Optional[str]


def sync_login(username: str, password: str, scratch_parent: Path) -> Login:
    """Trade the AnkiWeb ID and password for a host key.

    Uses a throwaway collection: ``sync_login`` doesn't touch its contents,
    it is simply how the ``anki`` library is reached. ``from None`` keeps
    the library's error, and the arguments it may carry, out of tracebacks.
    """
    scratch_parent.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="login-", dir=scratch_parent))
    col: Optional[Collection] = None
    try:
        col = Collection(str(folder / "collection.anki2"))
        auth = col.sync_login(username, password, None)
    except SyncError as e:
        if e.kind is SyncErrorKind.AUTH:
            raise LoginRejected() from None
        raise LoginUnavailable() from None
    except NetworkError:
        raise LoginUnavailable() from None
    finally:
        if col is not None:
            col.close()
        shutil.rmtree(folder, ignore_errors=True)
    return Login(hkey=auth.hkey, endpoint=auth.endpoint or None)
