# Sync direction: decision

Status: decided, nothing to build. Covers the TODO items "Consider options
for two-way sync" and "Reverse sync: Anki deck -> Notion page".

## Options compared

| Option | What it means | Cost | Risk |
| --- | --- | --- | --- |
| (a) One-way Notion -> Anki (today) | Anki only reads Notion; synced cards are overwritten with Notion's version | none | none: an Anki edit is only ever lost to Notion's own content |
| (b) One-way Anki -> Notion for stats only | Push review data (ease, lapses, "hard" flag) into a dedicated Notion column; never content | write capability on the integration; one PATCH per changed row per sync | low for content, but every push bumps the row's `last_edited_time`, which today is the only signal that a row changed: pushed stats would make every reviewed row look edited and force a re-download (breaks the ~12-request no-op sync) unless pushes are tracked and excluded |
| (c) Full two-way sync | Anki edits update Notion rows, Anki cards create rows | HTML -> Notion blocks converter, conflict tracking on both sides, write capability | high: HTML -> blocks is lossy (MathJax, tables, media uploads, callouts, toggles), and a bad merge overwrites the user's notes in Notion, which is where they actually write |

## Decision

**Keep (a): Notion stays the single source of truth.** Reverse sync and
two-way sync are not built.

Why:

- The add-on's safety rules all rest on one direction. "Never delete a card
  whose row may still exist", "an Anki edit is overwritten by Notion" and
  "a no-op sync makes ~12 requests" each assume Anki never writes to Notion.
  Two-way sync would need a second set of rules for the other side, and the
  side that loses data in a mistake would be Notion, where the notes live.
- The reverse of `htmlify` is lossy. Cards contain MathJax, rendered tables,
  callouts, toggles and Anki media names; turning that back into Notion
  blocks would silently degrade rows the user wrote by hand.
- Conflicts can't be detected cheaply. Notion's `last_edited_time` has
  minute precision and is the add-on's only change signal; Anki's note
  `mod` time changes on every sync write. Telling "edited in Anki" apart
  from "written by the add-on" needs per-note fingerprints on both sides.
- The integration would need write access ("Update content", "Insert
  content"), which makes a leaked token far more dangerous than today's
  read-only one.

What the user can do instead: edit in Notion. A card edited in Anki is
already overwritten on the next sync, and Edit > Undo Notion Sync restores
it if that happened by surprise.

## If this is ever revisited

Only option (b) is worth considering, and only as a strictly opt-in,
per-table setting that writes to one dedicated column the add-on owns (for
example "Anki stats"), never to the title or page content. It must:

1. remember the `last_edited_time` Notion returns from each PATCH, so the
   add-on's own write does not count as an edit and the row is not
   downloaded again;
2. batch pushes after the write step, outside the undo step and outside the
   collection lock;
3. never run when part of Notion could not be read.
