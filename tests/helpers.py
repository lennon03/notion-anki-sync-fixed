# -*- coding: utf-8 -*-
"""Fakes and builders shared by the tests and the worker's tests."""

from notion_sync_addon.notion_api import NotionError


class FakeTree:
    """children(block_id) -> pre-registered list of blocks."""

    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    def children(self, block_id):
        """Return the blocks registered under ``block_id``."""
        return self.mapping.get(block_id, [])


def _rt(text):
    return [
        {
            "type": "text",
            "plain_text": text,
            "text": {"content": text},
            "annotations": {
                "bold": False,
                "italic": False,
                "strikethrough": False,
                "underline": False,
                "code": False,
                "color": "default",
            },
        }
    ]


def _para(text):
    return {
        "id": "blk",
        "type": "paragraph",
        "has_children": False,
        "paragraph": {"rich_text": _rt(text)},
    }


def _image(block_id, name):
    return {
        "id": block_id,
        "type": "image",
        "has_children": False,
        "image": {
            "type": "file",
            "caption": [],
            "file": {"url": f"https://s3/x/{block_id}/{name}?sig=1"},
        },
    }


def _mention(label="Other page", kind="page"):
    return {
        "type": "mention",
        "plain_text": label,
        "annotations": {},
        "mention": {
            "type": kind,
            kind: {"id": "z", "url": "https://notion.so/z"},
        },
    }


def _bold(text):
    out = _rt(text)
    out[0]["annotations"]["bold"] = True
    return out


EDITED = "2026-11-04T10:51:00.000Z"


EDITED_TS = 1793789460.0  # EDITED as unix time


OLD = "2026-01-01T00:00:00.000Z"


class FakeNotion:
    """In-memory Notion: pages -> tables -> rows. Records row downloads."""

    def __init__(self):
        self.pages = {}  # page id -> {"edited", "title", "tables": [db id]}
        self.tables = (
            {}
        )  # db id -> {"title", "rows": {row id: [title, edited, blocks]}}
        self.fail = (
            set()
        )  # ("page"|"db"|"query"|"row", id): server error (503)
        self.media_fail = False
        self.downloads = []
        self.calls = []  # every request: (kind, id)
        self.ds = {}  # db id -> current data source id (default "ds-<db>")
        self.trashed = {}  # row id -> db id: rows moved to Notion's trash
        self.props = {}  # row id -> extra Notion property objects
        self.page_blocks = {}  # page id -> blocks written on the page itself
        # db id -> row ids listed by a second data source, "ds2-<db>"
        self.second_source = {}

    def __call__(self, token):
        """Stand in for the NotionClient class: the client is this fake."""
        return self

    def _ds_of(self, db):
        return self.ds.get(db, "ds-" + db)

    def get_page(self, pid):
        """Answer GET /pages for a page, a row or a trashed row."""
        self.calls.append(("page", pid))
        if ("page", pid) in self.fail:
            raise NotionError(f"page {pid} failed", 503, transient=True)
        if pid in self.pages:
            page = self.pages[pid]
            return {
                "last_edited_time": page["edited"],
                "properties": {
                    "t": {
                        "type": "title",
                        "title": [{"plain_text": page["title"]}],
                    }
                },
            }
        for db, table in self.tables.items():
            if pid in table["rows"]:
                return {
                    "id": pid,
                    "in_trash": False,
                    "parent": {"database_id": db},
                }
        if pid in self.trashed:
            return {
                "id": pid,
                "in_trash": True,
                "parent": {"database_id": self.trashed[pid]},
            }
        raise NotionError("object_not_found", 404)

    def children(self, bid):
        """Return a page's tables and blocks, or a row's blocks."""
        if bid in self.pages:
            self.calls.append(("blocks", bid))
            return [
                {
                    "id": db,
                    "type": "child_database",
                    "child_database": {"title": self.tables[db]["title"]},
                }
                for db in self.pages[bid]["tables"]
            ] + list(self.page_blocks.get(bid, []))
        for table in self.tables.values():
            if bid in table["rows"]:
                if ("row", bid) in self.fail:
                    raise NotionError("row failed", 503, transient=True)
                self.downloads.append(bid)
                return table["rows"][bid][2]
        return []

    def get_database(self, db):
        """Answer GET /databases: the title and the data sources."""
        self.calls.append(("db", db))
        if ("db", db) in self.fail:
            raise NotionError("GET database 503", 503, transient=True)
        if db not in self.tables:
            raise NotionError("object_not_found", 404)
        sources = [{"id": self._ds_of(db)}]
        if db in self.second_source:
            sources.append({"id": "ds2-" + db})
        return {
            "title": [{"plain_text": self.tables[db]["title"]}],
            "data_sources": sources,
        }

    def query_data_source(self, ds):
        """List the rows a data source holds."""
        self.calls.append(("query", ds))
        db = next((d for d in self.tables if self._ds_of(d) == ds), None)
        second = next(
            (d for d in self.second_source if "ds2-" + d == ds), None
        )
        if db is None and second is None:  # deleted or replaced
            raise NotionError("object_not_found", 404)
        if ("query", db or second) in self.fail:
            raise NotionError("query 503", 503, transient=True)
        if second is not None:
            db, ids = second, self.second_source[second]
        else:
            ids = set(self.tables[db]["rows"]) - set(
                self.second_source.get(db, ())
            )
        return [
            {
                "id": rid,
                "last_edited_time": r[1],
                "url": "",
                "properties": {
                    "Name": {
                        "type": "title",
                        "title": (r[3] if len(r) > 3 else _rt(r[0])),
                    },
                    **self.props.get(rid, {}),
                },
            }
            for rid, r in self.tables[db]["rows"].items()
            if rid in ids
        ]

    def download_file(self, url, path):
        """Write a small PNG, or fail when ``media_fail`` is set."""
        if self.media_fail:
            raise OSError("timed out")
        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + url.encode())


P1, P2 = "1" * 32, "2" * 32


DB1, DB2, DB3 = "d1" + "0" * 30, "d2" + "0" * 30, "d3" + "0" * 30


def _rid(n):
    """Return a Notion row id: 32 lowercase hex digits."""
    return f"f{n:03d}".ljust(32, "0")


class _Dialogs:
    """aqt.dialogs stand-in: closeAll waits for every registered instance."""

    def __init__(self):
        self.entries = {}

    def register_dialog(self, name, creator, instance=None):
        self.entries[name] = [creator, instance]

    def markClosed(self, name):
        self.entries[name] = [self.entries[name][0], None]

    def closeAll(self, onsuccess):
        for creator, instance in list(self.entries.values()):
            if instance is not None:
                instance.closeWithCallback(
                    lambda: all(i is None for _, i in self.entries.values())
                    and onsuccess()
                )
                return
        onsuccess()
