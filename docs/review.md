# Code review: bugs, improvements, simplifications

A read of the core (`notion_sync_addon`), the hosted worker and the site, in
that order. Each point has an id; a fix is a pull request stacked on the
review's own pull request and ticks its points here.

How far the read went: every line of `sync.py`, `notion_api.py`,
`htmlify.py`, `pagecards.py`, `__init__.py` and the worker; the site's routes
and `lib/`. Not read closely: `settings_ui.py`, `highlight.py`, `styles.py`,
the site's React components and the exporters' rendering (`html.ts`,
`docx.ts`).

Severity: **high** = wrong or lost card content, **medium** = wrong behaviour
in a common case, **low** = edge case or robustness.

## Core: bugs

- [x] **C1 (high) Equations are not HTML-escaped.** `htmlify.py` writes an
  equation's expression raw between `\(`/`\)` and `\[`/`\]`. `a<b` then opens a
  tag in Anki's webview and the rest of the formula (often of the card)
  disappears. Inline and block equations.
- [x] **C2 (high) Indented blocks under a paragraph, and the content of a
  toggle heading, are dropped.** `_render_block` downloads the children and
  then returns `<p>text</p>` or `<hN>text</hN>` without them. Indenting under a
  paragraph is common in Notion; that content never reaches the card.
- [x] **C3 (high) A table row's title goes into the Front field unescaped.**
  `_fetch_table` takes the title's plain text and `_row_fields` writes it as
  HTML (`fields = [title, back]`). A title such as `What is <div>?` or
  `a < b` loses text. Cards from normal pages escape theirs.
- [x] **C4 (low) A table whose header row is its only row ends with a
  stray `</tbody>`.**
- [x] **C5 (medium) `canonical_page_id` keeps an id's capital letters.** The
  same page pasted once in capitals and once in lowercase is read twice and
  cached under two keys. Its last fallback ("the last 32 hex characters of the
  whole string") turns arbitrary text into an id, which then fails as a Notion
  page instead of being reported as an invalid entry; the loop before it can
  never match anything the regular expression above it did not.
- [ ] **C6 (medium) Page mentions in a normal-page card are not re-checked
  hourly.** `_RecordingTree` only sees blocks fetched through `children()`;
  a page card's own blocks and question come from the page listing, so a
  mention there never marks the card volatile. Table rows do it right.
- [ ] **C7 (low) A page card's `fetched_at` is taken when the card is
  rendered, not when the page's blocks were read.** On a page with many cards
  the cache can claim content is newer than it is.
- [ ] **C8 (medium) Media is downloaded into memory, with no size limit.**
  `download_file` returns `resp.read()`; a large video can exhaust the worker's
  memory. The file is also written outside the `try`, so a full disk aborts
  the whole sync instead of failing one file.
- [x] **C9 (low) `get_block_children` uses `assert` for a missing cursor.**
  An `AssertionError` escapes every `except NotionError` and fails the sync.
- [ ] **C10 (low) A 2xx answer that is not JSON is marked transient but not
  retried.**
- [ ] **C11 (low) `_check_unlisted` asks `GET /pages/<id>` about page-card
  notes.** Their Source is a block id, so the answer is always 404 and they use
  up the 25 lookups a sync allows.
- [ ] **C12 (low) `_file_safe` cuts a long file name at 120 characters from
  the left, dropping the extension.**
- [ ] **C13 (low) `sync_every_minutes` holding text raises in the profile-open
  hook** (`int()` in `_schedule_autosync`).

## Core: improvements

- [ ] **I1 Only a database's first data source is read** (`data_source_of`).
  A database with several data sources loses the rows of the others.
- [ ] **I2 Rows are downloaded one after the other.** The client already keeps
  a connection per thread; three workers would make a first sync about three
  times faster within Notion's rate limit.
- [ ] **I3 A row moved to another table keeps its old deck.**
- [ ] **I4 A table's title is taken from the cached page listing**, so a
  renamed table is only noticed when its page's own edit time moves (to verify
  against Notion: whether renaming an inline database moves it).
- [ ] **I5 Titles lose their formatting**: bold, inline code and inline
  equations in a row title become plain text.

## Simplifications

- [x] **S1 Dead code.** `NotionClient._iterate`; the `child_database` branch
  of `_render_block` (the type is filtered out before); `_sniff_ext(...) or
  ".bin"`; the slug loop in `canonical_page_id`; `_reason()` (every
  `NotionError` has `reason`); the `bare_id` alias.
- [ ] **S2 The title of a Notion object is extracted in four places**
  (`page_title`, a loop in `_fetch_table`, `_resolve_table`,
  `export_read.describe`) and rich text is joined to plain text in three
  (`sync._plain`, `htmlify._plain`, `pagecards.rt_text`).
  *Partly done:* titles go through `title_rich_text`/`plain_text` in
  `notion_api.py`; `htmlify._plain` and `pagecards.rt_text` remain (they read
  `text.content`, not `plain_text`).
- [x] **S3 The worker calls the client's private `_request` for `/search` and
  `/users/me`** in three places. Two public methods remove that.
- [ ] **S4 `_apply_card_style` and `_apply_note_types` in `__init__.py` are
  the same forty lines twice.**
- [x] **S5 The cloze and Source regular expressions exist twice** (`sync.py`,
  `pagecards.py`).
- [ ] **S6 `_get_or_create_model` and `_get_or_create_cloze_model` differ by
  a field list and a template.**
- [ ] **S7 Renewing the Notion sign-in is written twice** (`api.renewed_token`,
  `Runner._current_notion_token`).
- [ ] **S8 `sync.py` is 2,800 lines and five things**: the cache file, the
  note type registry, reading Notion, choosing a card's kind, writing the
  collection. Splitting it along those lines changes no behaviour and makes
  each part testable alone. `_render_blocks` returns a five-value tuple that
  wants to be a small dataclass.

## Worker

- [ ] **W1 (medium) `GET /notion/pages` and the title lookup of `PUT /config`
  do not renew an expired Notion sign-in.** The export routes do; the page
  picker answers "Notion rejected this token" while a refresh token is stored.
- [ ] **W2 (low) `PUT /config` answers 500 when the stored token can't be
  decrypted.**
- [ ] **W3 (low) `POST`/`DELETE /connect` check that no run is active and then
  delete the local collection**; a scheduled run can start in between.
- [ ] **W4 (medium) The page picker reads at most 2,000 search results, rows
  included.** In a workspace with large tables the pages can be missing.
- [ ] **W5 (low) The "nothing to do" shortcut compares `(count, max(mod))` of
  the notes.** An edit that arrives from another device with an older `mod`
  is not noticed until the hourly full read.
- [ ] **W6 (improvement) The whole collection is exported as a backup on every
  full read**, at least hourly, and thrown away when nothing changed.
- [ ] **W7 (low) The scheduler logs a traceback every 30 seconds** when a
  stored secret can't be decrypted (`NotReady` raised from `tick`).
- [ ] **W8 (improvement) The hosted sync always uses the add-on's default
  options**; `sync_column`, `page_cards` and the others can't be set.
- [ ] **W9 (low) `table_rows` reads every row before refusing a table with
  more than 1,000.**
- [ ] **W10 (low) A run's log stops silently at 500 lines**, losing the
  closing `Timings` line.

## Site

- [ ] **B1 (medium) The proxy and sign-in routes parse the worker's answer
  with an unguarded `upstream.json()`.** While Railway wakes the worker the
  answer is an HTML error page, and the browser gets a 500 instead of "the
  sync worker can't be reached".
- [ ] **B2 (improvement) A push deploys without waiting for CI** (already
  noted in `hosted-sync.md`).
