# Notion Sync (official API)

Turn your Notion tables into Anki cards, and keep them in step with Notion.

Each Notion page you add becomes a deck, each table on it becomes a subdeck,
and each table row becomes a card: the row title is the front, the row's page
content is the back. Edit in Notion, sync, and the cards follow. Delete a row
and its card goes too.

This is a rewrite of the original add-on for the **official Notion API**. It no
longer needs the `token_v2` browser cookie. It is tested on Anki 26.9 (PyQt6).

## Features

- **Notion is the source of truth.** Synced cards are added, updated and
  removed to match Notion. Cards you made by hand are never touched, and
  decks are never deleted.
- **Fast.** After the first sync, only rows edited in Notion are downloaded.
  A sync with nothing new takes about a dozen small requests, however many
  cards you have.
- **Never freezes Anki.** Syncing runs in the background with no modal
  window. Progress shows on the toolbar link.
- **One undo step.** A whole sync is a single *Notion Sync* entry in
  Edit > Undo, and a sync that changes nothing leaves none.
- **Safe removal.** If part of Notion cannot be read (network error, page not
  shared, rate limit), the cards from that part are kept, and Settings shows
  what the last sync could not read. Nothing is deleted on a guess.
- **Rich content.** Text formatting, lists, to-dos, toggles, quotes, callouts,
  code blocks, tables, MathJax equations, images, audio, video and file
  attachments (downloaded into Anki's media folder), bookmarks and links.
- **AnkiWeb option.** Turn on *Sync with AnkiWeb* and Anki's own Sync button
  also pulls Notion, then uploads the result. It never loops, never runs while
  Anki is closing, and never runs after a failed AnkiWeb sync.
- **Auto-sync** every N minutes (off by default), a *Sync Now* shortcut
  (Ctrl+Shift+Y) and a *Full Resync* that re-reads every row.
- **A settings dialog** with a light and dark theme matching Anki.

## Setup

1. Create an integration at <https://www.notion.so/my-integrations> and copy
   its token.
2. In Notion, share each page you want to sync with that integration
   (the **...** menu on the page, then **Connections**).
3. In Anki open **Tools > Notion Sync > Settings**, paste the token and add each page by
   its full URL or 32-character id.
4. Press **Save & close**, then **Sync now**.

You can also provide the token through the `NOTION_API_TOKEN` environment
variable or a `.env` file instead of storing it in Anki's config.

## How pages map to cards

- A page you add becomes a deck.
- Each table (database) on that page, including tables inside columns,
  becomes a subdeck named after the table.
- Each row becomes one card using the *Notion Sync Card* note type:
  **Front** is the row title, **Back** is the row's content.
- Rows without a title become empty-front cards, or are skipped if you turn
  off *include empty cards* in Settings.

## Good to know

- The first sync after upgrading from an older version of this add-on re-reads
  every row once. This is expected.
- Configuration of the original add-on (cookie token, toggle-list import,
  `#tags` lines) is not used by this version. Enter an integration token and
  your pages again.
- The add-on only reads from Notion. It never writes to your workspace.
- Your token is stored in the add-on's config on your computer. It is never
  sent anywhere except to Notion.

## Not yet supported

Planned, not available today: cloze cards, tags from Notion, choosing your own
note type, custom card styling, syntax highlighting for code blocks.

## Links

- Source and issues: <https://github.com/lennon03/notion-anki-sync-fixed>
- Original project by Mikhail Knyazev: <https://github.com/9dogs/notion-anki-sync>
- Licence: GPL-3.0

---

### Notes for pasting into AnkiWeb

- Title: **Notion Sync (official API)**
- Tags: `notion`, `sync`, `import`
- Supported versions: set to the Anki versions you have tested (26.9 so far)
- AnkiWeb's description box takes simple HTML, not Markdown. Convert headings
  to `<h3>`, lists to `<ul><li>`, links to `<a href>` and code to `<code>`
  before pasting.
- Upload the `.ankiaddon` built by the *Build Plugin* workflow on a GitHub
  release. The add-on code in the folder is `notion_sync_addon`.
- Add `docs/settings.png` and `docs/supported-content.png` as screenshots.
