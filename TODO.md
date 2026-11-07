# TODO

## Research

- [ ] **Review 2anki for features worth borrowing.** Look through
  [2anki](https://2anki.net) ([source](https://github.com/2anki/2anki.net))
  and decide whether to implement any of its features here. It turns Notion
  pages into Anki decks too. Things to compare: card types such as cloze,
  reversed and basic-and-reversed cards, toggle-based cards, tag handling,
  card templates and styling, and how it handles media and nested pages.
  Note what to adopt and what to skip, then tick this off.

## Card types

- [x] **Support cloze cards.** Today every row becomes a basic Front/Back
  card. Decide how a cloze is marked in Notion: typed `{{c1::...}}` syntax,
  or formatting such as highlighted, underlined or inline-code text turned into
  clozes. Also decide whether that is set per table or per row. Cloze rows need
  their own note type (e.g. "Notion Sync Cloze") next to "Notion Sync Card",
  and switching a row between basic and cloze must not create duplicates.
  Overlaps with the 2anki review above: check how 2anki marks clozes first.
  - *Decision:* a row is a cloze when its title or page contains typed
    `{{c1::...}}` syntax (Anki's own syntax, unambiguous, works in titles),
    or when its "Card type" column says `Cloze`. Formatting-based markers
    (inline code, highlight) are rejected: they already mean something on
    existing cards. Per row, not per table. Cloze rows use the add-on's own
    "Notion Sync Cloze" type (Text, Back Extra, Source); a cloze in the title
    makes Text = title and Back Extra = page, a cloze in the page makes
    Text = title + page. A row switching between basic and cloze gets its
    old note replaced in the same undo step (remove + add): Anki's Change
    Note Type would keep the history but forces a one-way AnkiWeb sync,
    and the card's shape changes anyway. The row exists, so this is a
    replacement, never a loss of the row's card. A "Cloze" row with no
    cloze in it stays basic, with a warning.
- [x] **Overlapping cloze: hides one line at a time.** For quotes and other
  text to learn by heart: each card hides one line and shows the lines around
  it as cues, so you recite the text line by line. Decide how a row opts in
  (per table setting or a marker in Notion) and how lines are split (Notion
  blocks or line breaks). Anki has no built-in overlapping cloze, so this
  needs generated clozes or its own note type. Builds on the cloze item above.
  - *Decision:* opt in per row with the "Card type" column set to
    `Overlapping` (Notion stays the source of truth; no add-on setting).
    Lines are the page's top-level blocks (each list item is a line), and
    line breaks inside a paragraph split it too. It reuses the
    "Notion Sync Cloze" type: line N becomes `{{cN::...}}` inside
    `<div class="ns-line">`, and a `<style>` in the front template hides
    the lines after the asked one (`:has()`), so the front shows the
    previous lines as cues and the back shows the whole text. No extra note
    type, so no field-count limit.- [ ] **Cloze Overlapper-style options for overlapping cloze.** The current
  `Overlapping` card type always shows every previous line and hides the rest,
  and relies on CSS `:has()` to hide later lines (unverified on phones). Bring
  it closer to the Cloze Overlapper add-on: (a) how many previous lines and
  how many following lines are shown as cues (e.g. `Overlapping 2/1` in the
  "Card type" column; plain `Overlapping` keeps today's behaviour), (b) group
  several lines per cloze, (c) drop the `:has()` dependency if Anki's
  multi-id clozes (`{{c1,2::...}}`) can express the same hiding, so it also
  works in AnkiDroid and AnkiMobile. Same note type, existing rows must not
  change unless their Card type changes.

- [x] **Export to a chosen note type.** Let the user pick which Anki note type
  (and so which card design) a table's rows use, instead of always using
  "Notion Sync Card". Needs a per-table setting in the settings dialog and a
  mapping from Notion content to that note type's fields (title -> Front,
  page -> Back, other columns -> other fields). The add-on must keep finding
  its notes even though the note type differs. Related to the cloze items
  above: cloze is one specific note type.
  - *Decision:* the mapping lives in the collection config
    (`notionSync.noteTypes`), so every device that syncs with AnkiWeb uses
    the same one (add-on config is per device and two devices would fight).
    Settings lists each page's tables (from the cache) with a note type
    picker. Eligible types: standard (non-cloze) types with at least three
    fields, one named "Source", and no note of that type already holding a
    Source (so the user's own notes can never be mistaken for synced
    ones), or types the add-on already uses. Mapping: first field = title,
    second = page, Source = row id, any other field named like a Notion
    column = that column's text. Existing notes are never converted (that
    would force a one-way sync): new rows use the new type, existing notes
    keep syncing in their type; Browse > Change Note Type to one of the
    used types keeps them synced. Cloze rows always use "Notion Sync
    Cloze".
  - *Implemented, with these details:* the table -> type map is a
    `tables` entry (database id -> note type id) and every chosen type ever
    used is remembered in a `used` entry of the same `notionSync.noteTypes`
    dict, so old roles keep working and notes stay found after a switch.
    Saving the choice is one undo step (QueryOp, close guard, queued while a
    sync runs). Fields beyond the title and page are compared on each
    downloaded row so a changed column updates the note; the fingerprint
    still covers title, page and tags only. A cloze note turning basic is
    replaced by a note of the chosen type.

- [x] **Custom styling presets.** Let the user pick a card style in settings
  (e.g. a T3 Ocean light/dark look matching the settings dialog, a minimal
  one, Anki's default) and preview it before applying. Note: today
  `_get_or_create_model` resets the note type's CSS to the built-in style on
  every sync, so any CSS edited by hand in Anki is overwritten. Presets should
  fix that: only write CSS when the user picks a preset, and offer a "Custom
  (keep my CSS)" option.
  - *Decision:* presets Classic (today's look, default), T3 Ocean (light and
    dark, matches the settings dialog), Minimal, and Custom (keep my CSS).
    The add-on no longer rewrites the note type's CSS on every sync: CSS is
    written once when the user picks a preset in Settings (its own
    "Notion Sync: Card Style" undo step), and when a note type is created.
    Applies to the add-on's own note types only, never to a chosen user
    note type. The settings dialog shows a live preview of a sample card.

## Content

- [x] **Syntax highlighting for code blocks.** Today a Notion code block becomes
  plain monospace text: htmlify drops the block's `language`. Keep the language
  (e.g. `class="language-python"`) and highlight it. Options: highlight at sync
  time in Python (static HTML with inline colors, works offline and on every
  Anki client, but needs a highlighter such as Pygments bundled in the add-on),
  or ship a small JS highlighter (highlight.js / Prism) in the note type's
  template via collection media (lighter add-on, but runs on every card
  render). Colors should follow the card style, light and dark (see the
  styling presets item).
  - *Decision:* highlight at sync time in Python with a small built-in
    tokenizer (comments, strings, numbers, keywords for the common
    languages), not Pygments (several MB to vendor; Anki doesn't ship it)
    and not JS (needs template changes and runs on every card render).
    Output is static HTML that works offline on every Anki client; colors
    are inline `var(--ns-code-*, fallback)` so presets can retune them for
    dark mode. The language is kept as `class="language-xxx"`.
- [x] **Tags.** Turn Notion data into Anki tags, two possible sources:
  - a dedicated column (multi-select or text) whose values become tags;
  - strikethrough text inside the card's page: struck-through words become
    tags and are removed from the card content.
  Decide whether both are supported, how tag names are cleaned (Anki tags
  can't contain spaces), and that only tags the add-on added are removed when
  they disappear from Notion, so tags added by hand in Anki are kept.
  - *Decision:* a "Tags" column only (multi-select, select or text split on
    commas). Strikethrough-as-tags is rejected: it would remove text from
    existing cards where strikethrough is content. Tags are cleaned (spaces
    -> `_`, quotes dropped, `::` kept for hierarchy) and put under
    `Notion::`; the add-on owns exactly the tags under `Notion::`, so tags
    added by hand elsewhere are never touched, without any per-device
    bookkeeping. The column name is configurable (`tags_column`).
- [x] **Adjacent note: a "Context" column linking to another page.** A third
  column (a relation or URL property) points to a Notion page with background
  context for the row: an adjacent note shown with the card. Decide how it
  appears on the card: the linked page's content rendered in a collapsible
  section on the back, or just a link. Watch the extra API calls; the
  last_edited cache only tracks the row, so edits to the linked page won't be
  noticed unless they're tracked too.
  - *Decision:* a "Context" column (relation, or URL/text holding a Notion
    link) adds a collapsed `<details>` section on the back with the linked
    page's title and content; non-Notion URLs become a plain link. Linked
    pages are fetched only when the row itself is downloaded, once per sync
    however many rows share them. Rows with a context are re-checked once a
    day (the row's last-edited time doesn't move when the linked page
    changes), so a no-op sync makes no extra request. A linked page the
    integration can't read is shown as a link with a warning.

## Choosing what syncs

- [x] **Checkbox column to choose which rows sync.** A checkbox property
  (e.g. "Anki") in a Notion table decides which rows become cards. Unchecked
  rows are skipped. Decide whether unchecking a row that is already synced
  deletes, suspends or keeps its card, and what happens in tables that don't
  have the column (probably sync every row, as today).
  - *Decision:* a checkbox column named "Anki" (configurable,
    `sync_column`). Tables without it sync every row. An unchecked row
    never gets a card; if it already has one, its cards are suspended (not
    deleted: the row still exists) and tagged `notion-sync::unchecked`.
    Checking it again unsuspends those cards and removes the tag. Cards
    suspended by hand are never unsuspended.

## Sync direction

Today Notion is the single source of truth: the add-on only reads Notion and
overwrites synced cards with what Notion says.

- [x] **Consider options for two-way sync, eventually.** Compare the options
  before building anything: (a) keep one-way Notion -> Anki; (b) one-way
  Anki -> Notion for specific fields only (e.g. push review stats or "mark as
  hard" back to a Notion column, never content); (c) full two-way sync with
  conflict handling. Two-way breaks "Notion is the source of truth", so the
  choice decides how the item below is built, or whether it is built at all.
  - *Decision:* keep (a), one-way Notion -> Anki. See
    [docs/sync-direction.md](docs/sync-direction.md).
- [ ] **Reverse sync: Anki deck -> Notion page.** Push cards back to Notion:
  edits made in Anki update their Notion rows, and cards created in Anki become
  new rows (or a deck becomes a new Notion page/table). Open questions:
  - Conflicts: decide who wins when a card and its row both changed since the
    last sync (last-edited time on both sides, or always ask).
  - HTML -> Notion blocks conversion. The reverse of htmlify is lossy (MathJax,
    tables, media uploads), so decide which content is safe to push.
  - The integration needs Notion's "update/insert content" capabilities; today
    the add-on is read-only.
  - Keep it opt-in, per deck or table, so a mistake can't overwrite Notion
    notes.
  - [x] *Research done, decision: not built.* It breaks "Notion is
    the source of truth", the reverse of htmlify is lossy and the
    integration would need write access. See
    [docs/sync-direction.md](docs/sync-direction.md).

## Sync correctness

Notion should always win. Known gaps, deferred from the review of PR #1:

- [x] **Renamed Notion page or table doesn't move existing cards.** Deck
  names come from the page and table titles, but existing notes keep their
  cards in the old deck; only new rows land in the renamed deck. Decide
  whether a rename should move the cards (only those still in the old
  add-on-made deck, so decks the user rearranged are left alone) and whether
  to delete the empty old deck.
  - *Decision:* the cache remembers the deck each table's cards were put
    in. After a rename, synced cards still in that old deck move to the new
    one; cards the user moved elsewhere stay. The old deck is never deleted
    (the add-on never deletes decks); it is left for the user to remove.
- [x] **Renaming the note type or its Source field in Anki makes duplicates.**
  The add-on finds its notes by the "Notion Sync Card" note type and its
  "Source" field. If either is renamed, no synced note is found, every row is
  added again and the old notes are never removed. Remember the note type id
  (and look the Source field up by position or a stored name) instead of
  relying on names, or at least warn when the type is missing but notes with
  Notion sources exist.
  - *Decision:* the add-on's note type ids are stored in the collection
    config (synced via AnkiWeb) and looked up by id first, name second.
    Its own types use fixed field positions (Front/Text, Back/Back Extra,
    Source), so renaming a field changes nothing.
- [x] **Synced blocks and columns inside a row aren't rendered.** htmlify
  skips `synced_block`, `column_list`/`column`, `link_to_page` and
  `child_page` blocks, so that content never reaches the card. Rendering
  synced block copies needs their original's children; rows with copies are
  already re-checked every hour (their original can change without bumping
  the row's last-edited time), so rendering them is safe to add. If edits to
  an original must show up sooner than that, lower
  `VOLATILE_MAX_AGE_SECONDS` or track the originals' own last-edited times.
  Bump RENDER_VERSION when this changes.
  - *Decision:* synced block copies render their original's children
    (an original the integration can't read becomes a short note instead
    of failing the row), columns render side by side with wrapping on
    narrow screens, `link_to_page` and `child_page` become links (no extra
    request; child pages are not inlined). Volatile re-check stays hourly.
- [ ] **Old mixed-case media files stay behind.** Media file names are now
  lowercase (Anki on Windows stored them lowercased but handed back the
  mixed-case name on re-imports). On Linux/macOS, files imported by older
  versions under mixed-case names are no longer used once their rows are
  re-rendered; Tools > Check Media lists them as unused. Decide whether the
  add-on should delete `ns-*` files no synced card references.
  - *Decision:* don't delete them. Deleting media can't be undone, is not
    part of the Notion Sync undo step, and a user's own note may reference
    an `ns-*` file. Tools > Check Media > Delete Unused already does this
    safely and shows what goes. Documented in config.md.
- [ ] **Media import still holds the collection.** Identical re-downloaded
  files are no longer imported again, but new or changed files are imported
  inside the write step (about 2 ms per file on Windows), so a first sync of
  a page with hundreds of images keeps reviews waiting for a second or two.
  Importing media in the background step before the write, guarded against
  the profile closing, would shorten it.
  - *Decision:* new files are copied into the media folder by the
    background (Notion) step, which doesn't hold the collection; it stops
    as soon as the profile closes or changes. The write step then finds
    them in place and skips them. A file whose name exists with different
    bytes is still handed to Anki in the write step (rare; keeps Anki's
    renaming rules).

## Done

- [x] **Show tables that keep failing somewhere persistent.** Done: the cache
  file keeps what the last sync couldn't read, and the settings dialog shows
  it in a warning card above the Notion pages.

- [x] **Decide on the "Notion" toolbar link.** Decided in 1.2.1: when
  "Anki's Sync button also syncs Notion" is on there is no extra button;
  otherwise a one-click "Notion Sync" link sits right after Anki's Sync. The
  menu-bar item was removed.
