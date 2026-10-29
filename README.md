# Anki-Notion Sync (official API)

An [Anki](https://apps.ankiweb.net/) add-on that creates Anki decks from
[Notion](https://notion.so) pages using the official Notion API.

## How it works

- Configure pages in **Tools > Notion Sync > Settings**.
- Add either a full Notion page URL or its 32-character page id.
- Choose whether rows without titles should be added as empty-front cards
  (enabled by default).
- Each page becomes a deck named after that page.
- Each table becomes a subdeck, and each row becomes a card.
- Notion is the source of truth for synced cards: one sync updates cards and
  removes stale synced cards whose source rows no longer exist.
- Decks are never deleted or reorganized. You can add your own subdecks under
  a page deck and they will remain untouched.
- Manually created cards are never deleted, including cards using the
  add-on's note type without a Notion `Source` value.

## Content conversion

The add-on supports paragraphs, headings, nested lists, toggles, quotes,
callouts, code, MathJax equations, images, audio, video, attachments, tables,
links, mentions, bold, italic, underline, and strikethrough.

Card formatting uses Anki's normal card styling with left-aligned content.
No external or Notion stylesheet is loaded.

## Requirements

Create an internal integration at https://www.notion.so/my-integrations and
share each target page with it. Enter the token in the settings panel, or use
`NOTION_API_TOKEN` / a `.env` file.

Auto-sync is disabled by default and can be enabled in Settings with an
interval in minutes.

## Development

```bash
make fmt
make lint
make test
```

Unit tests do not require Anki installed.
