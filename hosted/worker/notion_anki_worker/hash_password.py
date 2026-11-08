"""Print an ``OWNER_PASSWORD_HASH`` for a password typed at the prompt."""

from __future__ import annotations

import getpass

from notion_anki_worker.crypto import hash_password

if __name__ == "__main__":
    print(hash_password(getpass.getpass("Owner password: ")))
