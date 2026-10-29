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

Use **Tools > Notion Sync > Settings** to edit these values. The add-on accepts
both full Notion URLs and plain 32-character page ids.

## Sync behavior

Notion is the source of truth for synced cards. Every sync updates existing
synced cards and removes stale synced cards whose source rows are no longer
present in Notion. Decks are never deleted or reorganized, so manually added
subdecks remain intact. Cards without a Notion `Source` value are also kept.
