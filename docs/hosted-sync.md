# Hosted sync

Runs the Notion -> Anki sync without Anki open on a desktop. The owner gives
the site an AnkiWeb login, signs in with Notion and picks pages; a worker keeps the
AnkiWeb collection in sync with Notion.

```
browser --cookie--> Vercel (Next.js) --bearer + session--> Railway (worker)
                                                              |-- api.notion.com
                                                              `-- AnkiWeb
```

The add-on is not changed. The worker imports `notion_sync_addon.sync` and
drives `prepare_sync` / `fetch_pages` / `apply_sync` the same way the add-on's
`__init__.py` does, against a headless `anki.collection.Collection`.

## One run

1. Open the local collection on the volume (`profile/`). If a `dirty` marker is
   there, an earlier run stopped between changing it and AnkiWeb accepting the
   change: discard it first.
2. `sync_collection` with the stored host key: a normal sync that pulls only
   what changed. An empty collection (the first run) is answered with
   `FULL_DOWNLOAD`, and that download writes only to the local folder. If
   AnkiWeb's collection was replaced in the meantime (a one-way sync from
   desktop), the local copy is discarded and downloaded again. Any other answer
   stops the run as "full sync needed", for example an AnkiWeb account that was
   never synced from a desktop.
3. **Shortcut.** Stop here if all of this holds: the page list and render
   version are the same, no note was added, edited or deleted, Notion was read
   in full less than an hour ago, and Notion's search shows nothing edited
   since the last full read. That costs one Notion request.
4. Read Notion. `prepare_sync` uses the persisted row cache, so unchanged rows
   are not downloaded again.
5. Export the collection (`.colpkg`, no media) as the backup of the state
   before this run, then `apply_sync`. The cache is written to the scratch
   folder for now. Notes last seen in a page that isn't selected, or of unknown
   origin, are protected: turning a page off never deletes its cards.
6. Nothing changed: drop the backup, no write to AnkiWeb.
7. `sync_collection` again, a normal sync that sends the changes. If AnkiWeb
   answers anything but "done" (it did not write, it asks for a full sync),
   stop and surface "full sync needed". The cache is not committed and the
   local copy is marked for discarding.
8. Commit the cache, keep the backup (newest 5). Media sync if the run added
   files, or a previous media sync did not finish.

Each run logs how long its steps took (`Timings: ...`).

**The worker never uploads a full collection.** `full_upload_or_download` is
called in one place, with `upload=False`. A test fails if anything calls it
with `upload=True`. The local copy never holds anything AnkiWeb lacks, other
than changes of a run still in progress, so it can always be thrown away.

Connecting a different AnkiWeb account, or forgetting the credentials, deletes
the local copy and the row cache.

The shortcut can't notice Notion's search lagging behind an edit, but it can't
lose one either: the time it compares with only moves on after a run that read
Notion for real. Edits that don't move any edit time (synced blocks, mentions)
wait for the hourly full read.

### Media

`collection.media` and `collection.media.db2` sit next to the collection in
`profile/`. Anki's media sync has to download what the server has before it
uploads, and media sync only runs when a run added files. The first such run
downloads the existing AnkiWeb media once, so the volume must fit it.

The database never exists without its folder: a database whose files are gone
would tell AnkiWeb that the files were deleted. A `pending` marker records
files that arrived but were not sent, so a run that stops early can't strand
them.

## Data model (SQLite and files on the volume, `DATA_DIR`)

| What | Where | Note |
| --- | --- | --- |
| `ankiweb_hkey`, `notion_token`, `notion_refresh_token` | `settings` table | AES-256-GCM, the key name is authenticated too |
| AnkiWeb endpoint, pages, schedule | `settings` table | not secret |
| Run history (status, stats, warnings, log) | `runs` table | newest 50, scrubbed of secrets |
| Row cache | `cache/notion-cache.json` | no secrets, written after a good push only |
| Backups | `backups/before-*.colpkg` | card content, no credentials |
| Collection and media | `profile/` | card content, no credentials; see above |
| Current run | `scratch/run-*` | deleted when the run ends, cleaned at start-up |

The AnkiWeb ID and password are not stored.

## API

All routes except `/health` and the signed backup download need
`Authorization: Bearer $WORKER_API_SECRET`. All except `/health`, `/session`
and the download also need `X-Session-Token`.

| Route | Purpose |
| --- | --- |
| `GET /health` | liveness, no details |
| `POST /session` | password -> session token (rate limited) |
| `GET /status` | connection flags, pages, schedule, runs, backups, needed action |
| `POST /connect` | `ankiweb: {username, password}` (rate limited); Notion connects only through its sign-in |
| `DELETE /connect` | forget the host key and the token |
| `POST /notion/oauth/start`, `/finish` | Sign in with Notion; the state is signed by the worker |
| `GET /notion/pages` | pages the token can read, without table rows |
| `GET /notion/export/page/{id}` | what an id is (page or table) and the tables on it |
| `GET /notion/export/table/{id}/rows` | a table's rows: id and title |
| `GET /notion/export/blocks/{id}` | a row's blocks with their children nested, for the exporter |
| `PUT /config` | page links, schedule |
| `POST /sync` | start a run (409 if one is running) |
| `POST /backups/{name}/link` | short-lived signed download path |
| `GET /backups/{name}/download` | the `.colpkg`, with `exp` and `sig` |

## Table exporter

The site also exports a Notion page's tables to PDF, Word or HTML (it came from
the separate `notion-philo-workflow` project). It lives at `/export` on the same
site and uses the Notion sign-in above: there is no token field and no token in
any environment variable.

```
browser --> /export, /api/export, /export/view (Vercel: renders)
                |-- GET /notion/export/... --> worker (holds the token) --> Notion
```

**Why the work is split this way.** The three `/notion/export/*` routes on the
worker only read: they look up a page's tables, a table's rows and a row's
blocks (children nested, up to 5000 blocks per row), and return Notion's JSON.
Rendering stays on the site. It needs KaTeX, highlight.js, `docx` and headless
Chromium, none of which have a Python equivalent that would render the same
documents, and Chromium would make the Railway image far heavier. What crosses
from the worker to the site is page *content*, for one request, never stored;
the token itself never leaves Railway. Every read passes the same secret and
session as the other routes, and Notion's own error text is replaced by fixed
messages, because it can quote the request.

- `POST /api/export` (PDF, Word) and `GET /export/view` (HTML, drawn live) check
  the session cookie first and the worker checks it again. `/api/export` also
  checks `Origin`. Table ids are validated before any worker call, and at most
  12 tables go in one export.
- `/export/view` is a page of Notion content, so it has its own
  Content-Security-Policy: no scripts at all, images from `https:` only,
  fonts as data URIs. Even if an escape were ever missed, nothing in it can run.
- KaTeX's stylesheet and the code font are inlined into
  `src/lib/export/assets.generated.ts` by `scripts/inline-export-assets.mjs`
  before every build (it is not committed), so exported documents are one
  self-contained file and no font file has to be found at run time.

**Embedding in Notion.** Notion shows embeds in a sandboxed iframe that blocks
downloads, and the session cookie (`HttpOnly`, `SameSite=Lax`) is not sent
inside a third-party frame anyway. So `/embed?id=<page id>` is only a launcher:
a card with one link that opens `/export?id=...` in a new tab, where the owner's
session applies (or the sign-in page does). The launcher reads no cookie, calls
no API and shows nothing from Notion. It is the only page the site lets another
origin frame: its `frame-ancestors` names `https://www.notion.so`,
`https://notion.so` and `https://*.notion.site`, and it has no `X-Frame-Options`
(which can't name an origin). Every other page keeps `frame-ancestors 'none'`.
An old `?token=` on an embed link is ignored.

## Security

- The AnkiWeb password goes through the worker once, into `col.sync_login`,
  and is not logged or stored. Only the host key is kept.
- Secrets are encrypted with `ENCRYPTION_KEY`, which exists only on Railway.
- Site -> worker needs the shared secret *and* a session. The session token is
  signed by the worker, whose key is derived from `ENCRYPTION_KEY`, so the
  secret held by Vercel can't open a session on its own. The site keeps the
  token in an `HttpOnly`, `SameSite=Lax` cookie (so the redirect back from Notion arrives signed in; every state-changing route also checks the `Origin` header); browsers never see the
  shared secret.
- The owner's password exists only as an scrypt hash, in `OWNER_PASSWORD_HASH`
  on Railway.
- `/session` is limited to 10 attempts per IP and 40 overall in 15 minutes;
  connecting AnkiWeb to 5 per IP and 10 overall.
- Validation errors never echo the request body (FastAPI's default does), and
  every log line and stored message passes through a scrubber that removes the
  secrets the process knows and anything shaped like a Notion token.
- Uvicorn's access log is off, so signed links aren't logged.

Single user for now: the data model has one owner, but every route already
goes through the session gate.

## Failure modes

| Failure | What happens | What the owner sees |
| --- | --- | --- |
| AnkiWeb asks for a full sync | stop, no write | "Full sync needed": sync from Anki desktop, then Sync now. Scheduled runs pause |
| AnkiWeb rejects the host key | stop | "Reconnect AnkiWeb". Scheduled runs pause |
| AnkiWeb unreachable or busy | run fails, AnkiWeb unchanged | failed run, next schedule retries |
| Notion unreadable (token, 5xx) | what was read is applied; cards of unread parts are kept (the add-on's own rule) | warnings on the run |
| Crash mid-run | start-up marks it interrupted and deletes the scratch folder; the next run discards a half-changed local copy | interrupted run |
| Media sync fails or times out | cards are already on AnkiWeb, retried next run | warning |
| A run makes bad changes | the backup from before it is kept | download it, in Anki desktop use File > Import, then sync and choose upload |

Reverting is deliberately manual: pushing an old collection is a full upload,
which the worker never does.

## Environment

Railway: `ENCRYPTION_KEY` (base64 of 32 bytes), `WORKER_API_SECRET`,
`OWNER_PASSWORD_HASH` (`python -m notion_anki_worker.hash_password`),
`DATA_DIR` (a volume, default `/data`). For Sign in with Notion, all three of
`NOTION_CLIENT_ID`, `NOTION_CLIENT_SECRET` and `NOTION_REDIRECT_URI` (the site's
`/api/notion/callback`); without them Notion can't be connected.

Vercel: `WORKER_URL`, `WORKER_API_SECRET`.

## Deployment

- Worker: Railway service `worker` in the project `tranquil-communication`,
  volume at `/data`, built from `hosted/worker/Dockerfile` with the repository
  root as context (`RAILWAY_DOCKERFILE_PATH`).
- Site: Vercel project `notion-anki-sync`, root directory `hosted/web`.

The repository is a Turborepo with two workspaces, `hosted/web` and
`hosted/worker` (`npm run build`, `lint`, `test`, `typecheck` at the root). A
push to `experimental` deploys whichever side it touches: Railway watches
`hosted/worker/**` and `notion_sync_addon/**`, and Vercel skips the build when
`turbo-ignore` finds `hosted/web` unaffected.
Neither host waits for CI, so a push that fails the checks still deploys.

**Railway's free plan forces the service to sleep when idle.** The scheduler
runs inside the worker, so while it sleeps no scheduled sync fires. Any request
wakes it (opening the site does), and an overdue sync then starts within about
30 seconds. For a schedule that runs unattended, move to a paid plan and turn
sleeping off, or have something call `/health` regularly.
