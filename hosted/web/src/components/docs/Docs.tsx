import type { ReactNode } from "react";

import { Mark, REPO_URL } from "~/components/landing/Landing";
import { Button, InlineButton } from "~/components/ui/button";
import { cn } from "~/lib/utils";

/*
 * The user guide. The same text lives in docs/guide.md for readers on GitHub:
 * change both together.
 */

const DOCS_BASE = `${REPO_URL}/blob/experimental`;

const SECTIONS: { id: string; title: string }[] = [
  { id: "idea", title: "The idea" },
  { id: "setup", title: "Set it up" },
  { id: "write", title: "Write cards in a page" },
  { id: "cloze", title: "Cloze cards" },
  { id: "front", title: "Long questions, hints, tags" },
  { id: "decks", title: "Pages and sub-pages" },
  { id: "tables", title: "Tables" },
  { id: "sync", title: "What a sync does" },
  { id: "settings", title: "Settings" },
  { id: "help", title: "When something looks wrong" },
];

function C({ children }: { children: ReactNode }) {
  return (
    <code className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[0.8125em] text-foreground">
      {children}
    </code>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="scroll-mt-20 border-t py-10 first:border-t-0 first:pt-0">
      <h2 id={id} className="mb-4 scroll-mt-20 text-2xl font-semibold tracking-tight">
        {title}
      </h2>
      <div className="space-y-4 text-[0.9375rem] leading-relaxed text-muted-foreground [&_strong]:font-semibold [&_strong]:text-foreground">
        {children}
      </div>
    </section>
  );
}

function Sub({ children }: { children: ReactNode }) {
  return <h3 className="pt-3 text-base font-semibold text-foreground">{children}</h3>;
}

function List({ items }: { items: ReactNode[] }) {
  return (
    <ul className="list-disc space-y-1.5 pl-5 marker:text-muted-foreground/60">
      {items.map((item, index) => (
        <li key={index}>{item}</li>
      ))}
    </ul>
  );
}

interface Side {
  front: ReactNode;
  back?: ReactNode;
}

/** What you type in Notion, and the cards it gives. */
function Example({ notion, cards, note }: { notion: string; cards: Side[]; note?: ReactNode }) {
  return (
    <figure className="mx-0 overflow-hidden rounded-xl border bg-card">
      <div className="grid md:grid-cols-2">
        <div className="border-b p-4 md:border-r md:border-b-0">
          <p className="mb-2 font-mono text-[0.6875rem] tracking-widest text-muted-foreground uppercase">
            In Notion
          </p>
          <pre className="overflow-x-auto font-mono text-[0.8125rem] leading-relaxed whitespace-pre-wrap text-foreground">
            {notion}
          </pre>
        </div>
        <div className="p-4">
          <p className="mb-2 font-mono text-[0.6875rem] tracking-widest text-muted-foreground uppercase">
            In Anki
          </p>
          <div className="space-y-2">
            {cards.map((card, index) => (
              <div key={index} className="rounded-lg border bg-background text-sm text-foreground">
                <div className="px-3 py-2">{card.front}</div>
                {card.back !== undefined && (
                  <div className="border-t border-dashed px-3 py-2 text-muted-foreground">
                    {card.back}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
      {note && (
        <figcaption className="border-t bg-muted/40 px-4 py-2.5 text-[0.8125rem] text-muted-foreground">
          {note}
        </figcaption>
      )}
    </figure>
  );
}

/** A real Notion page, as its owner sees it. The files are in public/docs. */
function Shot({ name, alt, caption }: { name: string; alt: string; caption: string }) {
  return (
    <figure className="mx-0 overflow-hidden rounded-xl border bg-white">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={`/docs/notion-${name}.png`}
        alt={alt}
        loading="lazy"
        className="block h-auto w-full max-w-[36rem]"
      />
      <figcaption className="border-t bg-muted/40 px-4 py-2.5 text-[0.8125rem] text-muted-foreground">
        {caption}
      </figcaption>
    </figure>
  );
}

function Hole({ dim = false }: { dim?: boolean }) {
  if (dim) {
    return (
      <span aria-label="hidden" className="inline-block h-[1em] w-12 rounded-sm bg-muted-foreground/30 align-middle" />
    );
  }
  return <span className="font-semibold text-primary">[...]</span>;
}

function Grid({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto rounded-xl border">
      <table className="w-full border-collapse text-left text-sm">
        <thead>
          <tr className="bg-muted/50">
            {head.map((cell) => (
              <th key={cell} className="px-3 py-2 font-semibold text-foreground">
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-t align-top">
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className="px-3 py-2">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Ext({ href, children }: { href: string; children: ReactNode }) {
  return (
    <InlineButton className="underline" render={<a href={href} />}>
      {children}
    </InlineButton>
  );
}

/** The public guide. Static: no session, no API call. */
export function Docs({ signedIn = false }: { signedIn?: boolean }) {
  return (
    <div className="flex min-h-svh flex-col bg-background text-foreground">
      <header className="surface-glass sticky top-0 z-50 border-b">
        <nav
          aria-label="Main"
          className="mx-auto flex h-14 w-full max-w-[1240px] items-center justify-between gap-3 px-5 sm:px-8"
        >
          <a
            href="/about"
            className="inline-flex items-center gap-2 rounded-sm text-base font-medium tracking-tight focus-visible:outline-2 focus-visible:outline-ring"
          >
            <Mark />
            Notion Sync
            <span className="font-normal text-muted-foreground">/ Guide</span>
          </a>
          <div className="flex items-center gap-1">
            <Button variant="ghost-muted" size="sm" render={<a href={REPO_URL} />}>
              GitHub
            </Button>
            <Button size="sm" render={<a href={signedIn ? "/" : "/login"} />}>
              {signedIn ? "Dashboard" : "Sign in"}
            </Button>
          </div>
        </nav>
      </header>

      <div className="mx-auto grid w-full max-w-[1240px] flex-1 gap-10 px-5 py-10 sm:px-8 lg:grid-cols-[13rem_minmax(0,46rem)] lg:gap-16 lg:py-14">
        <aside className="max-lg:hidden">
          <nav aria-label="On this page" className="sticky top-24">
            <p className="mb-3 font-mono text-[0.6875rem] tracking-widest text-muted-foreground uppercase">
              On this page
            </p>
            <ul className="space-y-1.5 text-sm">
              {SECTIONS.map((section) => (
                <li key={section.id}>
                  <a
                    href={`#${section.id}`}
                    className="text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
                  >
                    {section.title}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        </aside>

        <main id="main" className="min-w-0">
          <h1 className="mb-3 text-4xl font-semibold tracking-tight text-balance">
            Write notes in Notion. Review them in Anki.
          </h1>
          <p className={cn("mb-10 max-w-[38rem] text-lg leading-relaxed text-muted-foreground")}>
            This guide shows everything you can write in a Notion page and the card each thing
            becomes. Notion is always the original: you edit there, never in Anki.
          </p>

          <Section id="idea" title="The idea">
            <p>
              You pick one or more Notion pages. <strong>Each page becomes an Anki deck.</strong>{" "}
              Inside the page you write cards as ordinary text. Text that doesn&apos;t look like a
              card is left alone, so cards and normal notes can share a page.
            </p>
            <Example
              notion={`### What is the powerhouse of the cell?
The mitochondrion.

Q: What is the capital of Japan?
A: Tokyo

le chat :: the cat

{{Canberra}} is the capital of Australia.`}
              cards={[
                { front: "What is the powerhouse of the cell?", back: "The mitochondrion." },
                { front: "What is the capital of Japan?", back: "Tokyo" },
                { front: "le chat", back: "the cat" },
                {
                  front: (
                    <>
                      <Hole /> is the capital of Australia.
                    </>
                  ),
                },
              ]}
              note="One page, four cards."
            />
            <Shot
              name="biology"
              alt="A Notion page with a question heading, a Q and A pair, a one-liner and a cloze"
              caption="The same kind of page in Notion. The first paragraph is an ordinary note and gets no card."
            />
          </Section>

          <Section id="setup" title="Set it up">
            <Sub>As an Anki add-on</Sub>
            <ol className="list-decimal space-y-1.5 pl-5">
              <li>
                Create a Notion integration at{" "}
                <Ext href="https://www.notion.so/my-integrations">notion.so/my-integrations</Ext>{" "}
                and copy its token.
              </li>
              <li>
                In Notion, open each page you want to sync, then <strong>... &gt; Connections</strong>{" "}
                and add your integration. Its sub-pages are shared automatically.
              </li>
              <li>
                In Anki, open <strong>Tools &gt; Notion Sync &gt; Settings</strong>. Paste the token
                and add each page by its link.
              </li>
              <li>
                <strong>Save &amp; close</strong>, then <strong>Sync now</strong>.
              </li>
            </ol>
            <Sub>Hosted</Sub>
            <p>
              A site and a worker keep your AnkiWeb collection current while Anki is closed. See the{" "}
              <Ext href={`${DOCS_BASE}/docs/hosted-sync.md`}>hosted sync guide</Ext>.
            </p>
          </Section>

          <Section id="write" title="Write cards in a page">
            <p>There are four ways to write a card. Mix them freely on one page.</p>

            <Sub>1. A heading with the answer under it</Sub>
            <p>
              A <strong>heading 3</strong> or <strong>heading 4</strong> is a question. What is under
              it is the answer.
            </p>
            <Example
              notion={`### Why is the sky blue?
Air scatters blue light more than red.
This is called Rayleigh scattering.`}
              cards={[
                {
                  front: "Why is the sky blue?",
                  back: (
                    <>
                      Air scatters blue light more than red.
                      <br />
                      This is called Rayleigh scattering.
                    </>
                  ),
                },
              ]}
            />
            <List
              items={[
                <>
                  The answer ends at the next heading, at a divider (<C>---</C>), at a <C>Q:</C> line
                  or at a <C>front :: back</C> line.
                </>,
                "Heading 1 and heading 2 are never questions. Use them to organise the page.",
                "A heading with nothing under it is just a title, not a card.",
              ]}
            />

            <Sub>2. Q: and A:</Sub>
            <Example
              notion={`Q: What is the capital of Japan?
A: Tokyo`}
              cards={[{ front: "What is the capital of Japan?", back: "Tokyo" }]}
            />
            <List
              items={[
                <>
                  <C>Question:</C> and <C>Answer:</C> work too. Capitals don&apos;t matter.
                </>,
                <>
                  The answer continues on the next lines. <strong>An empty line ends the card.</strong>
                </>,
              ]}
            />

            <Sub>3. One line</Sub>
            <Example
              notion="le chat :: the cat"
              cards={[{ front: "le chat", back: "the cat" }]}
              note="Put a space on both sides of the ::"
            />

            <Sub>4. A sentence with a hole</Sub>
            <p>
              Wrap what should be hidden in double braces. See <a href="#cloze" className="underline">Cloze cards</a>.
            </p>

            <Sub>What is not a card</Sub>
            <p>
              Everything else: plain paragraphs, lists, images, heading 1 and 2, toggles. Your
              ordinary notes stay ordinary notes.
            </p>
          </Section>

          <Section id="cloze" title="Cloze cards">
            <p>
              A cloze hides part of a sentence. Each pair of braces becomes its own card, numbered
              for you in reading order.
            </p>
            <Example
              notion="{{Canberra}} is the capital of {{Australia}}."
              cards={[
                {
                  front: (
                    <>
                      <Hole /> is the capital of Australia.
                    </>
                  ),
                },
                {
                  front: (
                    <>
                      Canberra is the capital of <Hole />.
                    </>
                  ),
                },
              ]}
              note="Each card hides one hole and shows the other answers."
            />
            <Grid
              head={["You write", "You get"]}
              rows={[
                [<C key="a">{"{{Paris}}"}</C>, "A hole, numbered automatically."],
                [<C key="b">{"{{Paris::a city}}"}</C>, "A hole that shows the hint “a city”."],
                [
                  <C key="c">{"{{c1::Paris}} is in {{c1::France}}"}</C>,
                  "Anki's own syntax: you choose the number. Holes with the same number are hidden together, on one card.",
                ],
              ]}
            />
            <p>
              A cloze works in a paragraph, a list item, a quote or a callout.{" "}
              <strong>One block is one note.</strong> To keep several lines in one note, put them
              under a heading 3. Text formatted as code is never turned into a cloze.
            </p>

            <Shot
              name="cloze"
              alt="A Notion page with a cloze line ending in [hide], a cloze under a heading and a numbered list"
              caption="In Notion: a cloze line with [hide], two cloze lines kept together under a heading, and a list to recite."
            />

            <Sub>Keep the other holes hidden</Sub>
            <p>
              To hide <strong>every</strong> hole, so no other answer gives yours away, write{" "}
              <C>[hide]</C> at the end of the line or of the heading.
            </p>
            <Example
              notion="{{Paris}} is in {{France}}, {{Rome}} is in {{Italy}}. [hide]"
              cards={[
                {
                  front: (
                    <>
                      <Hole /> is in <Hole dim />, <Hole dim /> is in <Hole dim />.
                    </>
                  ),
                },
              ]}
              note="Four cards. On each, one hole is asked and the others are greyed out. The back shows everything."
            />

            <Sub>A list to recite</Sub>
            <p>
              If the answer is a <strong>numbered list</strong>, you get one card per item. Each
              card hides one item and shows the ones before it, so you learn the list in order.
            </p>
            <Example
              notion={`### Phases of mitosis
1. Prophase
2. Metaphase
3. Anaphase`}
              cards={[
                {
                  front: (
                    <>
                      <span className="font-medium">Phases of mitosis</span>
                      <br />
                      Prophase
                      <br />
                      <Hole />
                    </>
                  ),
                },
              ]}
              note="The second of three cards."
            />
            <p>Options go in square brackets after the question:</p>
            <Grid
              head={["Write", "Meaning"]}
              rows={[
                [
                  <C key="a">### Steps [2/1]</C>,
                  "Show 2 items before and 1 after the hidden one. [0/0] shows only the hole.",
                ],
                [<C key="b">### Steps [x2]</C>, "Hide 2 items at a time."],
                [<C key="c">### Poem [overlap]</C>, "Recite it line by line, even without a numbered list."],
                [<C key="d">### Steps [basic]</C>, "Don't split it: one card with the whole list on the back."],
              ]}
            />
          </Section>

          <Section id="front" title="Long questions, hints, tags">
            <Sub>Put more on the front</Sub>
            <p>
              Normally the front is only the question line. To show more on the front (a text to
              read, a picture, some code), write a line starting with <C>Back:</C>. Everything{" "}
              <strong>above</strong> it goes on the front.
            </p>
            <Example
              notion={`### Name this theorem
In a right triangle, the square of the
hypotenuse equals the sum of the squares
of the other two sides.
Back: Pythagoras' theorem.`}
              cards={[
                {
                  front: (
                    <>
                      <span className="font-medium">Name this theorem</span>
                      <br />
                      In a right triangle, the square of the hypotenuse equals the sum of the squares
                      of the other two sides.
                    </>
                  ),
                  back: "Pythagoras' theorem.",
                },
              ]}
              note="Without a Back: line, the front stays short."
            />
            <Shot
              name="geometry"
              alt="A Notion page with a long question, a Back: line, struck-through tags and a Hint heading"
              caption="In Notion: a long front, the Back: line, two struck-through tags and a hint."
            />
            <p>
              With <C>Q:</C> and <C>A:</C> you don&apos;t need it: whatever sits between the two
              lines is on the front.
            </p>

            <Sub>A hint</Sub>
            <p>
              A heading 4 called <C>Hint</C> starts a collapsed section on the back. It ends at a
              divider or at the end of the card.
            </p>

            <Sub>Tags</Sub>
            <p>
              <strong>Strike through</strong> a word in a card to make it an Anki tag:{" "}
              <s>biology, cells</s> gives <C>Notion::biology</C> and <C>Notion::cells</C>, and the
              struck text leaves the card. Tags you add by hand in Anki are kept.
            </p>
          </Section>

          <Section id="decks" title="Pages and sub-pages">
            <List
              items={[
                <>
                  Each page you add in the settings is a <strong>deck</strong>, named after the page.
                </>,
                <>
                  A <strong>sub-page</strong> inside it is a <strong>subdeck</strong>, up to 5 levels
                  deep. <C>Biology</C> with the sub-pages <C>Cells</C> and <C>Genetics</C> gives{" "}
                  <C>Biology</C>, <C>Biology::Cells</C> and <C>Biology::Genetics</C>.
                </>,
                "You only add the top page. New sub-pages are found on the next sync.",
                "Rename a page in Notion and its cards move to the renamed deck. Cards you moved yourself stay.",
              ]}
            />
          </Section>

          <Section id="tables" title="Tables">
            <p>
              A Notion table (database) on a synced page works too. Use one when you need columns: a
              checkbox to choose which rows sync, or your own note type with extra fields.
            </p>
            <List
              items={[
                "The table is a subdeck of its page. Each row is a card.",
                "The row's title is the front. The page inside the row is the back.",
                <>
                  Cloze braces, the <C>Back:</C> line, <C>#### Hint</C> and strikethrough tags work
                  inside a row.
                </>,
              ]}
            />
            <Grid
              head={["Column", "Type", "What it does"]}
              rows={[
                [
                  <C key="a">Anki</C>,
                  "Checkbox",
                  "Unticked rows get no card. Unticking a row suspends its card. It is not deleted.",
                ],
                [
                  <C key="b">Card type</C>,
                  "Select",
                  "Basic, Cloze, Cloze hide, or Overlapping (with the list options: Overlapping 2/1 x2).",
                ],
              ]}
            />
          </Section>

          <Section id="sync" title="What a sync does">
            <List
              items={[
                <>
                  <strong>Notion wins.</strong> A synced card edited in Anki is overwritten at the
                  next sync. Edit in Notion.
                </>,
                "Edit a card's text in Notion: the same Anki note is updated and its review history is kept.",
                "Delete the question in Notion: the note is removed from Anki.",
                "Your own cards and your decks are never touched.",
                "If Notion can't be reached, nothing is deleted.",
                "A sync that changes cards is one undo step: Edit > Undo Notion Sync.",
              ]}
            />
            <p>
              A card belongs to the Notion block of its question. Rewording it is fine. Deleting it
              and typing it again makes a new card with no history.
            </p>
          </Section>

          <Section id="settings" title="Settings">
            <p>
              In Anki: <strong>Tools &gt; Notion Sync &gt; Settings</strong> for the token, the pages,
              automatic sync, the card style and note types. A few more options are in the
              add-on&apos;s <strong>Config</strong>:
            </p>
            <Grid
              head={["Key", "Default", "Meaning"]}
              rows={[
                [<C key="a">page_cards</C>, "true", "Read cards written in pages."],
                [<C key="b">sub_pages</C>, "true", "Read sub-pages as subdecks."],
                [<C key="c">include_empty_cards</C>, "true", "Rows without a title become cards."],
                [<C key="d">sync_column</C>, "Anki", "Name of the checkbox column."],
                [<C key="e">card_type_column</C>, "Card type", "Name of the card type column."],
              ]}
            />
            <p>
              Every detail of a sync is in the{" "}
              <Ext href={`${DOCS_BASE}/notion_sync_addon/config.md`}>add-on reference</Ext>.
            </p>
          </Section>

          <Section id="help" title="When something looks wrong">
            <Grid
              head={["You see", "Check"]}
              rows={[
                [
                  "A card is missing",
                  "Is the question a heading 3 or 4? Does the Q: have an A: before an empty line? Is there a space on both sides of the :: ?",
                ],
                ["A whole page gives nothing", "Is the page shared with your integration (... > Connections)?"],
                ["A title became a card", "Make it a heading 1 or 2, or put nothing under it."],
                ["A numbered list was split into cards", "Add [basic] after the question."],
                ["{{something}} became a cloze by accident", "Format it as inline code."],
                [
                  "A warning after a sync",
                  "Settings lists what the last sync could not read. Nothing was deleted because of it.",
                ],
              ]}
            />
          </Section>
        </main>
      </div>

      <footer className="border-t py-8">
        <div className="mx-auto flex w-full max-w-[1240px] flex-wrap items-center justify-between gap-x-6 gap-y-4 px-5 text-[0.8125rem] text-muted-foreground sm:px-8">
          <p className="flex items-center gap-2.5">
            <Mark />
            GPL-3.0. Not affiliated with Notion or Anki.
          </p>
          <Ext href={`${DOCS_BASE}/docs/guide.md`}>This guide on GitHub</Ext>
        </div>
      </footer>
    </div>
  );
}
