# Anki💗Notion Sync (official API)

It's an [Anki](https://apps.ankiweb.net/) addon that creates Anki decks from
[Notion](https://notion.so) pages using the **official Notion API** — no more
fragile HTML export scraping.

## How it works

- You configure a set of Notion **page ids** (or page URLs) in the addon config.
- Every page becomes an Anki **deck**, named after the page.
- Every **table** (inline database) on a page becomes a **subdeck**, named after the table.
- Every **row** of a table becomes one card:
  - **Front** = the row's title property
  - **Back** = the full content of that row's page, converted to HTML.

## Content conversion

The row pages can be marked up freely; everything below is supported:

- paragraphs, headings, dividers, nested lists (bulleted / numbered / to-do)
- toggle blocks → `<details>`, quotes, callouts (with colors)
- **code blocks** → clean `<pre><code>` with a dark theme
- **math blocks & inline LaTeX** → Anki-native MathJax (`\\[...\\]` / `\\(...\\)`)
- **images / audio / video / attachments** → automatically downloaded and added
  to your Anki media collection
- inline tables → HTML tables, mentions & links, code formatting, bold/italic/etc.

## Requirements

### Notion API token

Notion API tokens are issued from Notion's official integration flow; you can
find all the details on `notion.so/my-integrations`.

Create an internal integration, copy its token, and **share your target pages
with the integration** (page menu → Connections → select your integration).

Provide the token in one of these places (first match wins):

1. Addon config: Tools → Add-ons → "Notion Sync (official API)" → Config → `notion_token`
2. Environment variable `NOTION_API_TOKEN`
3. A `.env` file containing `NOTION_API_TOKEN=...`: inside the addon's
   `user_files/` folder, next to the addon, in the CWD, or in your home directory.

### Notion page ids

Open the page in a browser; the 32 hex characters at the end of the URL are the
page id. You can also paste the full URL — it is parsed automatically:

```
https://app.notion.com/p/Philo-Histoire-1bbf4336b2b480f4a0b6fc07ce4ee5e4
```

## Configuration

Edit the config from Anki: `Tools ➡ Add-ons ➡ Notion Sync (official API) ➡ Config`

```json
{
  "notion_token": "",
  "target_deck": "Notion Sync",
  "notion_pages": [
    { "page_id": "1bbf4336b2b480f4a0b6fc07ce4ee5e4", "recursive": false }
  ],
  "sync_every_minutes": 0
}
```

- `target_deck`: parent deck for everything (default `"Notion Sync"`).
- `notion_pages`: ids or URLs; `recursive: true` also turns direct sub-pages
  into nested decks.
- `sync_every_minutes`: background auto-sync interval, `0` disables it.

## Menus (Tools ➡ Notion Sync)

- **Sync Now**: fetch from Notion and import; existing cards are updated.
- **Sync && Remove Obsolete**: same, but cards whose Notion source is gone are deleted.
- **Preview Structure**: dry run — shows the deck tree, row counts and media
  without writing anything.

Cards are tracked by their Notion page id (stored in a hidden `Source` field),
so repeated syncs update in place instead of creating duplicates.

## Development

```bash
make fmt    # black + isort
make lint   # black, isort, flake8, pydocstyle, mypy
make test   # pytest with coverage
```

Unit tests do not require Anki installed (the collection/aqt layer is imported
lazily).

## Inspiration

This project replaces the original toggle-scraping addon and is inspired by a
great [Notion to Anki](https://github.com/alemayhu/Notion-to-Anki).
