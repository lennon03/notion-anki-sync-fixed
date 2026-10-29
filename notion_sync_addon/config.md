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
- `autosync`: `false` by default; set to `true` to enable background syncing.
- `sync_every_minutes`: interval in minutes when `autosync` is enabled.

Use **Tools > Notion Sync > Settings** to edit these values. The add-on accepts
both full Notion URLs and plain 32-character page ids.

## Sync behavior

Notion is the single source of truth. Every sync updates existing cards and
removes cards whose source rows are no longer present in Notion.
