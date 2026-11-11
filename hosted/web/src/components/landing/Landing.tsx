import { ArrowUpRight, Check } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";

import { HeroDemo } from "~/components/landing/HeroDemo";
import { SettingsGroup, SettingsRow } from "~/components/settings/settingsLayout";
import { Badge } from "~/components/ui/badge";
import { Button, InlineButton } from "~/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "~/components/ui/table";
import { cn } from "~/lib/utils";

import styles from "./landing.module.css";

const REPO_URL = "https://github.com/lennon03/notion-anki-sync-fixed";
// These docs are on the branch the site deploys from, not yet on main.
const HOSTED_DOCS_URL = `${REPO_URL}/blob/experimental/docs/hosted-sync.md`;
const ADDON_DOCS_URL = `${REPO_URL}/blob/experimental/notion_sync_addon/config.md`;

const CONTAINER = "mx-auto w-full max-w-[1240px] px-5 sm:px-8";
const SECTION = "border-t py-20 sm:py-28";
const SECTION_TEXT = "text-base leading-relaxed text-muted-foreground sm:text-lg";

const rise = (delayMs: number) => ({ "--rise-delay": `${delayMs}ms` }) as CSSProperties;

const CHIPS: { x: string; y: string; rotate: string; delay: number; content: ReactNode }[] = [
  { x: "-34rem", y: "5rem", rotate: "-7deg", delay: 250, content: "{{c1::Paris}}" },
  { x: "33rem", y: "3.5rem", rotate: "6deg", delay: 350, content: "Q: Why?" },
  { x: "-32rem", y: "16rem", rotate: "5deg", delay: 450, content: <s>ethics</s> },
  { x: "34rem", y: "15rem", rotate: "-5deg", delay: 550, content: "### Question?" },
];

const RUN_MODES: { title: string; lead: string; snippet: string; points: string[] }[] = [
  {
    title: "Anki add-on",
    lead: "Lives inside Anki desktop and writes to your collection directly.",
    snippet: "Tools > Notion Sync > Settings",
    points: [
      "Syncs on demand, on an interval, or with Anki's own Sync button.",
      "Every sync that changes cards is one undo step.",
      "Uses a Notion integration token you create.",
    ],
  },
  {
    title: "Hosted",
    lead: "A site and a worker keep your AnkiWeb collection current while Anki is closed.",
    snippet: "Notion → worker → AnkiWeb",
    points: [
      "Sign in with Notion and pick the pages.",
      "Runs from every 15 minutes to once a day, or when you press the button.",
      "Run history, warnings and backups on one dashboard.",
    ],
  },
];

const FEATURES: { title: string; text: string; snippet: ReactNode }[] = [
  { title: "Basic cards", text: "The row title is the front. Its page is the back.", snippet: "row → card" },
  {
    title: "Cards from plain text",
    text: "A normal page works too: headings, question and answer, or one-liners.",
    snippet: "front :: back",
  },
  {
    title: "Cloze",
    text: "Anki's own syntax, in a title or a page.",
    snippet: "{{c1::Canberra}}",
  },
  {
    title: "Overlapping cloze",
    text: "Hides one line and shows the ones around it, so you recite text line by line.",
    snippet: "Overlapping 2/1 x2",
  },
  {
    title: "Tags from strikethrough",
    text: "Struck-through words become tags and leave the card.",
    snippet: <s>ethics, kant</s>,
  },
  { title: "Hints", text: "A collapsed section on the back of the card.", snippet: "#### Hint" },
  {
    title: "Per-row checkbox",
    text: "Untick a row and its card is suspended, not deleted.",
    snippet: "Anki ☑",
  },
  {
    title: "Code highlighting",
    text: "Coloured at sync time, so it works offline on every Anki client.",
    snippet: "language-python",
  },
  {
    title: "Math",
    text: "Notion equations, rendered by Anki's MathJax.",
    snippet: "\\(e^{i\\pi} + 1 = 0\\)",
  },
  {
    title: "Images",
    text: "Copied from Notion into Anki's media, so they stay with your cards.",
    snippet: "collection.media",
  },
  {
    title: "Your note types",
    text: "Pick a note type per table in the add-on's settings.",
    snippet: "title → first field",
  },
  {
    title: "Card styles",
    text: "Classic, T3 Ocean or Minimal. Or keep your own CSS.",
    snippet: "Custom (keep my CSS)",
  },
];

const SAFEGUARDS: {
  title: string;
  text: string;
  badge: string;
  variant?: "success" | "outline";
}[] = [
  {
    title: "AnkiWeb password, used once",
    text: "It passes through the worker to get a login key. It is never stored or logged.",
    badge: "Not stored",
    variant: "success",
  },
  {
    title: "Secrets encrypted on the worker",
    text: "The AnkiWeb key and Notion tokens stay there. The site never holds them.",
    badge: "AES-256-GCM",
  },
  {
    title: "A backup before any change",
    text: "The collection is exported before a run writes to it. The latest five are kept.",
    badge: "Newest 5",
  },
  {
    title: "No full uploads",
    text: "The worker only sends normal syncs. A test fails if anything tries otherwise.",
    badge: "Never",
    variant: "success",
  },
  {
    title: "Notion is only read",
    text: "Changes flow one way, into Anki. Turning a page off never deletes its cards.",
    badge: "One-way",
  },
  {
    title: "One owner",
    text: "One password, kept only as a hash. No accounts, no sign-up.",
    badge: "scrypt",
  },
];

const EXPORT_POINTS = [
  "Choose the tables, the format and the text size.",
  "PDF and Word download as files. HTML opens as a page you can print.",
  "An embed launcher opens the exporter from inside a Notion page.",
];

function Mark() {
  return (
    <svg aria-hidden viewBox="0 0 32 32" className="size-5 shrink-0">
      <rect width="32" height="32" rx="7" fill="#0f6e6e" />
      <path
        d="M10 23V9l12 14V9"
        fill="none"
        stroke="#fff"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

interface AccountLink {
  label: string;
  href: string;
}

function AccountButton({ size, link }: { size: "sm" | "xl"; link: AccountLink }) {
  return (
    <Button size={size} render={<a href={link.href} />}>
      {link.label}
    </Button>
  );
}

function SectionHead({
  id,
  eyebrow,
  title,
  children,
}: {
  id: string;
  eyebrow: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="max-w-[45rem]">
      <p className={cn(styles.eyebrow, "mb-3.5")}>{eyebrow}</p>
      <h2 id={id} className={cn(styles.display, styles.sectionTitle, "mb-4.5")}>
        {title}
      </h2>
      <p className={cn(SECTION_TEXT, "max-w-[38.75rem]")}>{children}</p>
    </div>
  );
}

function Points({ items }: { items: string[] }) {
  return (
    <ul className="space-y-2.5">
      {items.map((item) => (
        <li key={item} className="flex gap-2.5 text-sm leading-normal text-muted-foreground">
          <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-primary" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

function ExportSample() {
  return (
    <figure className="m-0">
      <div className="dialog-glass rounded-2xl border p-3 sm:p-4">
        <div className="rounded-xl border bg-card p-5 sm:p-7">
          <p className="text-lg font-semibold tracking-tight text-card-foreground">Reading notes</p>
          <p className="mt-0.5 text-xs text-muted-foreground">Exported from a Notion table</p>
          <div className="mt-5 rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Term</TableHead>
                  <TableHead>Note</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow className="hover:bg-transparent">
                  <TableCell className="font-medium">Euler</TableCell>
                  <TableCell className="whitespace-normal text-muted-foreground">
                    <i>e</i>
                    <sup>
                      <i>iπ</i>
                    </sup>{" "}
                    + 1 = 0
                  </TableCell>
                </TableRow>
                <TableRow className="hover:bg-transparent">
                  <TableCell className="font-medium">Code</TableCell>
                  <TableCell className="font-mono whitespace-normal text-muted-foreground">
                    <span className="text-primary">print</span>(&quot;hello&quot;)
                  </TableCell>
                </TableRow>
                <TableRow className="hover:bg-transparent">
                  <TableCell className="font-medium">Maxim</TableCell>
                  <TableCell className="whitespace-normal text-muted-foreground">
                    A rule you act on
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
          <div className="mt-5 flex flex-wrap gap-1.5">
            <Badge>PDF</Badge>
            <Badge variant="outline">Word</Badge>
            <Badge variant="outline">HTML</Badge>
          </div>
        </div>
      </div>
      <figcaption className="sr-only">A sample table as the exporter lays it out.</figcaption>
    </figure>
  );
}

/** The public home page. Static: no session, no API call, nothing of the owner's. */
export function Landing({ signedIn = false }: { signedIn?: boolean }) {
  const account: AccountLink = signedIn
    ? { label: "Dashboard", href: "/" }
    : { label: "Sign in", href: "/login" };
  return (
    <div className="flex min-h-svh flex-col overflow-x-clip bg-background text-foreground">
      <a
        href="#main"
        className="sr-only rounded-md bg-popover px-3 py-2 text-sm focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-[60] focus:outline-2 focus:outline-ring"
      >
        Skip to content
      </a>

      <header className={cn(styles.nav, "surface-glass sticky top-0 z-50 border-b border-transparent")}>
        <nav aria-label="Main" className={cn(CONTAINER, "flex h-14 items-center justify-between gap-3")}>
          <a
            href="/"
            className="inline-flex items-center gap-2 rounded-sm text-base font-medium tracking-tight focus-visible:outline-2 focus-visible:outline-ring"
          >
            <Mark />
            Notion Sync
          </a>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost-muted"
              size="sm"
              className="max-sm:hidden"
              render={<a href={HOSTED_DOCS_URL} />}
            >
              Docs
            </Button>
            <Button variant="ghost-muted" size="sm" render={<a href={REPO_URL} />}>
              GitHub
            </Button>
            <AccountButton size="sm" link={account} />
          </div>
        </nav>
      </header>

      <main id="main" className="flex flex-1 flex-col">
        <section aria-labelledby="hero-title" className="relative overflow-hidden pt-16 pb-20 sm:pt-20">
          <div aria-hidden className={styles.heroGrid} />
          <div aria-hidden className={styles.chips}>
            {CHIPS.map((chip) => (
              <span
                key={chip.x}
                className={cn(
                  styles.chip,
                  "rounded-lg border bg-popover px-3 py-2 font-mono text-[0.8125rem] text-muted-foreground shadow-sm",
                )}
                style={
                  {
                    "--chip-x": chip.x,
                    "--chip-y": chip.y,
                    "--chip-rotate": chip.rotate,
                    "--rise-delay": `${chip.delay}ms`,
                  } as CSSProperties
                }
              >
                {chip.content}
              </span>
            ))}
          </div>

          <div className={cn(CONTAINER, "relative text-center")}>
            <h1
              id="hero-title"
              className={cn(styles.display, styles.heroTitle, styles.rise, "mx-auto mb-5.5 max-w-[20ch]")}
            >
              Turn your Notion notes into Anki cards.
            </h1>
            <p
              className={cn(
                styles.rise,
                "mx-auto mb-9 max-w-[45rem] text-base leading-relaxed text-balance text-muted-foreground sm:text-lg",
              )}
              style={rise(90)}
            >
              Notion Sync reads your pages and tables and keeps matching flashcards up to date. Run
              it as an Anki add-on, or hosted, so AnkiWeb stays current with Anki closed.
            </p>
            <div className={cn(styles.rise, "mb-4 flex flex-wrap justify-center gap-2.5")} style={rise(180)}>
              <AccountButton size="xl" link={account} />
              <Button variant="outline" size="xl" render={<a href={REPO_URL} />}>
                View on GitHub
                <ArrowUpRight aria-hidden />
              </Button>
            </div>
            <p className={cn(styles.rise, "mb-14 text-[0.8125rem] text-muted-foreground")} style={rise(240)}>
              Sign-in is for this site&apos;s owner. There&apos;s no sign-up.{" "}
              <InlineButton
                tone="muted"
                className="underline"
                render={<a href={`${REPO_URL}/fork`} />}
              >
                Fork it
              </InlineButton>{" "}
              to run your own.
            </p>
            <div className={cn(styles.rise, "mx-auto max-w-[1100px]")} style={rise(320)}>
              <HeroDemo />
            </div>
          </div>
        </section>

        <section aria-labelledby="run-title" className={SECTION}>
          <div className={CONTAINER}>
            <SectionHead id="run-title" eyebrow="Two ways to run it" title="With Anki open, or without it.">
              Both run the same sync code. Notion stays the source of truth, and each page becomes
              a deck, each table a subdeck, each row a card.
            </SectionHead>
            <div className="mt-10 grid gap-4 md:grid-cols-2">
              {RUN_MODES.map((mode) => (
                <article key={mode.title} className="flex flex-col rounded-xl border bg-card p-6 sm:p-7">
                  <h3 className="text-lg font-semibold tracking-tight text-card-foreground">
                    {mode.title}
                  </h3>
                  <p className="mt-1.5 text-sm leading-normal text-muted-foreground">{mode.lead}</p>
                  <code className="my-5 block overflow-x-auto rounded-lg border bg-code px-3 py-2.5 text-xs whitespace-nowrap text-foreground">
                    {mode.snippet}
                  </code>
                  <Points items={mode.points} />
                </article>
              ))}
            </div>
          </div>
        </section>

        <section aria-labelledby="cards-title" className={SECTION}>
          <div className={CONTAINER}>
            <SectionHead id="cards-title" eyebrow="Cards" title="Write the note. The card follows.">
              Most of it is set up in Notion itself. A table without the extra columns just works.
            </SectionHead>
            <div className="mt-10 grid gap-px overflow-hidden rounded-xl border bg-border sm:grid-cols-2 lg:grid-cols-3">
              {FEATURES.map((feature) => (
                <div key={feature.title} className="flex flex-col bg-card p-5 sm:p-6">
                  <h3 className="text-sm font-semibold text-card-foreground">{feature.title}</h3>
                  <p className="mt-1 mb-4 flex-1 text-sm leading-normal text-muted-foreground">
                    {feature.text}
                  </p>
                  <code className="self-start rounded-sm bg-muted px-1.5 py-0.5 text-xs text-foreground">
                    {feature.snippet}
                  </code>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section aria-labelledby="safe-title" className={SECTION}>
          <div className={cn(CONTAINER, "grid gap-10 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-16")}>
            <SectionHead
              id="safe-title"
              eyebrow="Hosted sync"
              title="Your collection is not a test environment."
            >
              The worker stops rather than guesses. If AnkiWeb asks for a full sync, nothing changes
              until you decide which side to keep.
            </SectionHead>
            <SettingsGroup className="self-start">
              {SAFEGUARDS.map((item) => (
                <SettingsRow
                  key={item.title}
                  title={item.title}
                  description={item.text}
                  control={<Badge variant={item.variant ?? "outline"}>{item.badge}</Badge>}
                />
              ))}
            </SettingsGroup>
          </div>
        </section>

        <section aria-labelledby="export-title" className={SECTION}>
          <div className={cn(CONTAINER, "grid items-center gap-10 lg:grid-cols-2 lg:gap-16")}>
            <div>
              <SectionHead id="export-title" eyebrow="Table Exporter" title="Need it on paper?">
                Export a Notion page&apos;s tables to PDF, Word or HTML, with code highlighted and
                math set by KaTeX.
              </SectionHead>
              <div className="mt-6">
                <Points items={EXPORT_POINTS} />
              </div>
            </div>
            <ExportSample />
          </div>
        </section>

        <section aria-labelledby="closing-title" className={SECTION}>
          <div className={cn(CONTAINER, "text-center")}>
            <h2 id="closing-title" className={cn(styles.display, styles.closingTitle, "mx-auto max-w-[16ch]")}>
              Stop retyping your notes.
            </h2>
            <p className={cn(SECTION_TEXT, "mx-auto mt-5 mb-9 max-w-[35rem]")}>
              One owner per site, and the code is yours to fork under the GPL.
            </p>
            <div className="flex flex-wrap justify-center gap-2.5">
              <AccountButton size="xl" link={account} />
              <Button variant="outline" size="xl" render={<a href={`${REPO_URL}/fork`} />}>
                Fork on GitHub
                <ArrowUpRight aria-hidden />
              </Button>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t py-8">
        <div className={cn(CONTAINER, "flex flex-wrap items-center justify-between gap-x-6 gap-y-4")}>
          <p className="flex items-center gap-2.5 text-[0.8125rem] text-muted-foreground">
            <Mark />
            GPL-3.0. Not affiliated with Notion or Anki.
          </p>
          <ul className="flex flex-wrap gap-x-5 gap-y-2 text-[0.8125rem]">
            {[
              ["GitHub", REPO_URL],
              ["Hosted sync docs", HOSTED_DOCS_URL],
              ["Add-on docs", ADDON_DOCS_URL],
              [account.label, account.href],
            ].map(([label, href]) => (
              <li key={label}>
                <InlineButton tone="muted" className="font-normal" render={<a href={href} />}>
                  {label}
                </InlineButton>
              </li>
            ))}
          </ul>
        </div>
      </footer>
    </div>
  );
}
