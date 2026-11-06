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

- [ ] **Support cloze cards.** Today every row becomes a basic Front/Back
  card. Decide how a cloze is marked in Notion: typed `{{c1::...}}` syntax,
  or formatting such as highlighted, underlined or inline-code text turned into
  clozes. Also decide whether that is set per table or per row. Cloze rows need
  their own note type (e.g. "Notion Sync Cloze") next to "Notion Sync Card",
  and switching a row between basic and cloze must not create duplicates.
  Overlaps with the 2anki review above: check how 2anki marks clozes first.
- [ ] **Overlapping cloze: hides one line at a time.** For quotes and other
  text to learn by heart: each card hides one line and shows the lines around
  it as cues, so you recite the text line by line. Decide how a row opts in
  (per table setting or a marker in Notion) and how lines are split (Notion
  blocks or line breaks). Anki has no built-in overlapping cloze, so this
  needs generated clozes or its own note type. Builds on the cloze item above.
- [ ] **Export to a chosen note type.** Let the user pick which Anki note type
  (and so which card design) a table's rows use, instead of always using
  "Notion Sync Card". Needs a per-table setting in the settings dialog and a
  mapping from Notion content to that note type's fields (title -> Front,
  page -> Back, other columns -> other fields). The add-on must keep finding
  its notes even though the note type differs. Related to the cloze items
  above: cloze is one specific note type.

## Content

- [ ] **Tags.** Turn Notion data into Anki tags, two possible sources:
  - a dedicated column (multi-select or text) whose values become tags;
  - strikethrough text inside the card's page: struck-through words become
    tags and are removed from the card content.
  Decide whether both are supported, how tag names are cleaned (Anki tags
  can't contain spaces), and that only tags the add-on added are removed when
  they disappear from Notion, so tags added by hand in Anki are kept.
- [ ] **Adjacent note: a "Context" column linking to another page.** A third
  column (a relation or URL property) points to a Notion page with background
  context for the row: an adjacent note shown with the card. Decide how it
  appears on the card: the linked page's content rendered in a collapsible
  section on the back, or just a link. Watch the extra API calls; the
  last_edited cache only tracks the row, so edits to the linked page won't be
  noticed unless they're tracked too.

## Choosing what syncs

- [ ] **Checkbox column to choose which rows sync.** A checkbox property
  (e.g. "Anki") in a Notion table decides which rows become cards. Unchecked
  rows are skipped. Decide whether unchecking a row that is already synced
  deletes, suspends or keeps its card, and what happens in tables that don't
  have the column (probably sync every row, as today).

## Sync direction

Today Notion is the single source of truth: the add-on only reads Notion and
overwrites synced cards with what Notion says.

- [ ] **Consider options for two-way sync, eventually.** Compare the options
  before building anything: (a) keep one-way Notion -> Anki; (b) one-way
  Anki -> Notion for specific fields only (e.g. push review stats or "mark as
  hard" back to a Notion column, never content); (c) full two-way sync with
  conflict handling. Two-way breaks "Notion is the source of truth", so the
  choice decides how the item below is built, or whether it is built at all.
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

## Done

- [x] **Decide on the "Notion" toolbar link.** Decided in 1.2.1: when
  "Anki's Sync button also syncs Notion" is on there is no extra button;
  otherwise a one-click "Notion Sync" link sits right after Anki's Sync. The
  menu-bar item was removed.
