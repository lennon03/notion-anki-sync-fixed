# Hosted sync

Runs the Notion -> Anki sync without Anki open on a desktop. The owner gives
the site an AnkiWeb login, a Notion token and page links; a worker keeps the
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
| `POST /connect` | `ankiweb: {username, password}` and/or `notion_token` (AnkiWeb part rate limited) |
| `DELETE /connect` | forget the host key and the token |
| `POST /notion/oauth/start`, `/finish` | Sign in with Notion; the state is signed by the worker |
| `GET /notion/pages` | pages the token can read, without table rows |
| `PUT /config` | page links, schedule |
| `POST /sync` | start a run (409 if one is running) |
| `POST /backups/{name}/link` | short-lived signed download path |
| `GET /backups/{name}/download` | the `.colpkg`, with `exp` and `sig` |

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
`/api/notion/callback`); without them the site offers pasting a token only.

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
