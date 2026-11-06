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
- `notion_pages`: list of page URLs or ids. An entry that isn't a Notion page
  link or id is skipped with a warning (and shown in the settings dialog);
  while it is there, cards of pages that are no longer listed are kept, in
  case that entry is one of them with a mangled link.
- `include_empty_cards`: add rows without a title as empty-front cards; enabled
  by default.
- `sync_every_minutes`: background sync interval in minutes. `0` disables
  auto-sync (the default).
- `sync_with_ankiweb` ("Anki's Sync button also syncs Notion"): after Anki
  syncs with AnkiWeb, also pull changes from Notion, then upload them with a
  second AnkiWeb sync if anything changed (only when AnkiWeb answers that a
  normal sync is needed; a failed AnkiWeb sync never triggers it). When on,
  the separate "Notion Sync" toolbar link is hidden. Off by default.

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
intact. Only notes whose `Source` is a Notion row id (32 hex characters) are
synced notes: a "Notion Sync Card" note you add yourself, with an empty
`Source` or anything else in it (e.g. a book reference), is never touched.
If part of Notion can't be read (network error, rate limit, a table that is
gone), the cards that came from that page or table are kept; the rest of the
sync still happens. The settings dialog lists what the last sync couldn't
read; cards deleted there in Notion are removed once a sync can read it
again. If the add-on doesn't know which table a card came from (its cache
file was deleted), it asks Notion about that row: a row in Notion's trash
has its card removed, any other answer keeps the card.

Only rows edited in Notion since the last sync are downloaded again; the
others are recognised by Notion's built-in last-edited time, so no extra
column is needed. Pages that weren't edited reuse their list of tables. As a
safety net, rows edited in the last few minutes, rows containing content that
can change without Notion noticing (synced block copies, mentions of pages,
databases or people, including in table cells and titles) once an hour and,
spread over time, every row older than one to two weeks are downloaded again.
**Full Resync** re-downloads every row; cards whose content is unchanged are
left alone.

Notion's edit times are compared with this computer's clock. A clock running
more than five minutes fast can make a row edited right after a sync look
unchanged; such a row is caught up when its cache entry expires (one to two
weeks) or with **Full Resync**.

A sync that changes cards is a single undo step: **Edit > Undo Notion Sync**
reverts it, and the next sync brings the cards back in line with Notion. A
sync that changes nothing adds no undo step.

Reading Notion runs in the background and doesn't hold the collection. Writing
the cards does: while it runs (usually well under a second; longer for the
first sync of a big page with many images), Anki finishes the import before
it saves a review or an edit, and **Undo** pressed during it undoes the whole
sync. Writing in smaller pieces would split the sync into several undo steps,
so it is done in one go. Closing Anki or the profile during it waits until
the cards are written.

The list of downloaded rows is kept in the add-on's `user_files` folder (one
file per profile), not in the collection, so it is never uploaded to AnkiWeb.
Deleting it is safe: the next sync just downloads every row again.
