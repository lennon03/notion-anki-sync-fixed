"""Start the worker: ``python -m notion_anki_worker``."""

from __future__ import annotations

import uvicorn
from notion_anki_worker.api import create_app
from notion_anki_worker.config import Settings
from notion_anki_worker.redact import configure_logging


def main() -> None:
    """Run the API server until killed."""
    configure_logging()
    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",
        port=settings.port,
        log_config=None,  # through the scrubbing handler
        access_log=False,  # signed download links are in query strings
    )


if __name__ == "__main__":
    main()
