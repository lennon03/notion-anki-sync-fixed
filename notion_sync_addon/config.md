# Notion Sync (official API)

Creates decks from your Notion workspace using the **official Notion API**
(`2025-09-03`) and imports them directly into your collection.

## Structure

- Every configured Notion **page** becomes a **deck** named after the page,
  under `target_deck`.
- Every **table** (inline database) on the page becomes a **subdeck**.
- Every **row** of a table becomes a card:
  - **Front** = the row's title property
  - **Back** = the full content of that row's page, converted to HTML
    (paragraphs, lists, toggles, quotes, callouts, tables, code blocks,
    MathJax for backend/in-block LaTeX, imported images/sound/pdf).

## Config

- `notion_token`: internal integration token
  (https://www.notion.so/my-integrations). Share your pages with the
  integration! Leave empty to use the `NOTION_API_TOKEN` environment
  variable or a `.env` file containing `NOTION_API_TOKEN=...` next to
  the addon (or in `user_files/`).
- `target_deck`: parent deck, default `"Notion Sync"`.
- `notion_pages`: list of `{ "page_id": "<id-or-url>", "recursive": true|false }`.
  Page ids can be raw ids, dashed ids or page URLs.
  `recursive` also turns direct sub-pages of the page into nested decks.
- `sync_every_minutes`: background auto-sync interval, `0` disables.

## Menus (Tools > Notion Sync)

- **Sync Now**: fetch & import, keep notes already removed from Notion untouched.
- **Sync & Remove Obsolete**: same, but also deletes notes whose Notion source
  was deleted.
- **Preview Structure**: dry run showing decks/tables/row counts, no writes.

Notes are identified by their Notion page id stored in the hidden `Source`
field, so repeated syncs update existing cards instead of duplicating them.
