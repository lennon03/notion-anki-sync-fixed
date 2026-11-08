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

1. Create an empty collection in a scratch folder under the volume.
2. `sync_collection` with the stored host key. An empty collection is always
   answered with `FULL_DOWNLOAD` by AnkiWeb, and that download is how a run
   gets the collection: it writes only to the scratch folder. Any other answer
   (for example an AnkiWeb account that was never synced from a desktop) stops
   the run as "full sync needed".
3. Read Notion. `prepare_sync` uses the persisted row cache, so unchanged rows
   are not downloaded again.
4. Export the collection (`.colpkg`, no media) as the backup of the state
   before this run, then `apply_sync`. The cache is written to the scratch
   folder for now.
5. Nothing changed: stop, drop the backup, no write to AnkiWeb.
6. `sync_collection` again, a normal sync that sends the changes. If AnkiWeb
   answers anything but "done" (it did not write, it asks for a full sync),
   stop and surface "full sync needed". The cache is not committed.
7. Commit the cache, keep the backup (newest 5). Media sync if the run added
   files, or a previous media sync did not finish.
8. Close the collection and delete the scratch folder.

**The worker never uploads a full collection.** `full_upload_or_download` is
called in one place, with `upload=False`, on an empty scratch collection. A
test fails if anything calls it with `upload=True`.

### Media

`collection.media` and `collection.media.db2` are the one part of the
collection folder that outlives a run, moved in and out of the scratch folder.
Anki's media sync has to download what the server has before it uploads, so a
fresh folder per run would re-download all of the AnkiWeb media every time.
Media sync only runs when a run added files. The first such run downloads the
existing AnkiWeb media once, so the volume must fit it.

The database never moves without its folder: a database whose files are gone
would tell AnkiWeb that the files were deleted. A `pending` marker in the store
records files that arrived but were not sent, so a run that stops early can't
strand them.

## Data model (SQLite and files on the volume, `DATA_DIR`)

| What | Where | Note |
| --- | --- | --- |
| `ankiweb_hkey`, `notion_token` | `settings` table | AES-256-GCM, the key name is authenticated too |
| AnkiWeb endpoint, pages, schedule | `settings` table | not secret |
| Run history (status, stats, warnings, log) | `runs` table | newest 50, scrubbed of secrets |
| Row cache | `cache/notion-cache.json` | no secrets, written after a good push only |
| Backups | `backups/before-*.colpkg` | card content, no credentials |
| Media | `media/` | see above |
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
  token in an `HttpOnly`, `SameSite=Strict` cookie; browsers never see the
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
| Crash mid-run | start-up marks it interrupted, restores media, deletes the scratch folder | interrupted run |
| Media sync fails or times out | cards are already on AnkiWeb, retried next run | warning |
| A run makes bad changes | the backup from before it is kept | download it, in Anki desktop use File > Import, then sync and choose upload |

Reverting is deliberately manual: pushing an old collection is a full upload,
which the worker never does.

## Environment

Railway: `ENCRYPTION_KEY` (base64 of 32 bytes), `WORKER_API_SECRET`,
`OWNER_PASSWORD_HASH` (`python -m notion_anki_worker.hash_password`),
`DATA_DIR` (a volume, default `/data`).

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
