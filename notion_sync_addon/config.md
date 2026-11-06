# Notion Sync (official API)

Creates decks from your Notion workspace using the official Notion API
(`2025-09-03`) and imports them directly into your collection.

## Structure

- Every configured Notion page becomes an Anki deck named after the page.
- Every table on the page becomes a subdeck.
- Every row of a table becomes a card with the title on the front and the
  row page content converted to HTML on the back.

## Config

- `notion_token`: internal integration token. Leave empty to use the
  `NOTION_API_TOKEN` environment variable or a `.env` file.
- `notion_pages`: list of page URLs or ids.
- `include_empty_cards`: add rows without a title as empty-front cards; enabled
  by default.
- `sync_every_minutes`: background sync interval in minutes. `0` disables
  auto-sync (the default).
- `sync_with_ankiweb` ("Anki's Sync button also syncs Notion"): after Anki
  syncs with AnkiWeb, also pull changes from Notion, then upload them with a
  second AnkiWeb sync if anything changed. When on, the separate "Notion Sync"
  toolbar link is hidden. Off by default.

Use **Tools > Notion Sync > Settings** (or the add-on's **Config** button) to
edit these values. The add-on accepts
both full Notion URLs and plain 32-character page ids.

## Sync behavior

Notion is the source of truth for synced cards. Every sync updates existing
synced cards and removes stale synced cards whose source rows are no longer
present in Notion. A synced card edited in Anki is overwritten with Notion's
version on the next sync, and a deleted one is recreated. If the same row was
added twice (e.g. on two devices before they synced), the oldest card is kept.
Decks are never deleted or reorganized, so manually added subdecks remain
intact. Cards without a Notion `Source` value are also kept.
If part of Notion can't be read (network error, rate limit, a table that is
gone), the cards that came from that page or table are kept; the rest of the
sync still happens.

Only rows edited in Notion since the last sync are downloaded again; the
others are recognised by Notion's built-in last-edited time, so no extra
column is needed. Pages that weren't edited reuse their list of tables. As a
safety net, rows edited in the last few minutes, rows containing content that
can change without Notion noticing (synced block copies, page mentions) and,
spread over time, every row older than one to two weeks are downloaded again.
**Full Resync** re-downloads every row.

A sync that changes cards is a single undo step: **Edit > Undo Notion Sync**
reverts it, and the next sync brings the cards back in line with Notion. A
sync that changes nothing adds no undo step.

The list of downloaded rows is kept in the add-on's `user_files` folder (one
file per profile), not in the collection, so it is never uploaded to AnkiWeb.
Deleting it is safe: the next sync just downloads every row again.
