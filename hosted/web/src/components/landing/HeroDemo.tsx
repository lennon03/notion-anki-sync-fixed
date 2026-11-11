import { ArrowRight, Check } from "lucide-react";
import type { ReactNode } from "react";

import { VisibleMotion } from "~/components/landing/VisibleMotion";
import { Badge } from "~/components/ui/badge";
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

const ROWS = [
  {
    title: "What does the categorical imperative ask?",
    type: "Basic",
    synced: true,
    row: styles.rowOne,
  },
  {
    title: "The capital of Australia is {{c1::Canberra}}.",
    type: "Cloze",
    synced: true,
    row: styles.rowTwo,
  },
  { title: "Ozymandias, opening lines", type: "Overlapping 1/0", synced: true, row: styles.rowThree },
  { title: "Ideas to sort later", type: "Basic", synced: false, row: undefined },
];

const CARDS: {
  type: string;
  tag: string;
  step: string;
  question: ReactNode;
  answer: ReactNode;
}[] = [
  {
    type: "Basic",
    tag: "Notion::ethics",
    step: styles.stepOne!,
    question: "What does the categorical imperative ask?",
    answer: "Act only on a maxim you could will to be a universal law.",
  },
  {
    type: "Cloze",
    tag: "Notion::geography",
    step: styles.stepTwo!,
    question: (
      <>
        The capital of Australia is <span className="font-semibold text-primary">[...]</span>.
      </>
    ),
    answer: (
      <>
        The capital of Australia is <span className="font-semibold text-primary">Canberra</span>.
      </>
    ),
  },
  {
    type: "Overlapping",
    tag: "Notion::poetry",
    step: styles.stepThree!,
    question: (
      <>
        I met a traveller from an antique land,
        <br />
        <span className="font-semibold text-primary">[...]</span>
      </>
    ),
    answer: "Who said: two vast and trunkless legs of stone",
  },
];

function PaneHeader({ source, name }: { source: string; name: string }) {
  return (
    <div className="flex h-11 items-center gap-2 border-b px-4 text-xs">
      <span className="font-semibold text-foreground">{source}</span>
      <span className="truncate text-muted-foreground">{name}</span>
    </div>
  );
}

function SyncColumn({ synced }: { synced: boolean }) {
  return (
    <span
      className={cn(
        "flex size-4 items-center justify-center rounded-[.25rem] border",
        synced ? "border-primary bg-primary text-primary-foreground" : "border-input bg-popover",
      )}
    >
      {synced ? <Check aria-hidden className="size-3" /> : null}
      <span className="sr-only">{synced ? "Ticked" : "Not ticked"}</span>
    </span>
  );
}

/** A Notion table on one side, the cards it makes on the other. Sample content. */
export function HeroDemo() {
  return (
    <figure className="m-0">
      <VisibleMotion className="dialog-glass grid overflow-hidden rounded-2xl border text-left lg:grid-cols-[minmax(0,1.3fr)_auto_minmax(0,1fr)]">
        <div className="min-w-0">
          <PaneHeader source="Notion" name="Study notes / Reading" />
          <div className="px-2 pt-1 pb-2">
            <Table className="text-xs sm:text-[0.8125rem]">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="text-muted-foreground">Name</TableHead>
                  <TableHead className="text-muted-foreground">Card type</TableHead>
                  <TableHead className="text-muted-foreground">Anki</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {ROWS.map((row) => (
                  <TableRow
                    key={row.title}
                    className={cn(styles.row, row.row, "hover:bg-transparent")}
                  >
                    <TableCell
                      className={cn(
                        "py-3 whitespace-normal",
                        !row.synced && "text-muted-foreground",
                      )}
                    >
                      {row.title}
                    </TableCell>
                    <TableCell
                      className={cn(
                        "font-mono text-xs max-sm:whitespace-normal",
                        row.synced ? "text-foreground" : "text-muted-foreground",
                      )}
                    >
                      {row.type}
                    </TableCell>
                    <TableCell>
                      <SyncColumn synced={row.synced} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>

        <div className="flex items-center justify-center border-y bg-muted/60 py-2 lg:border-x lg:border-y-0 lg:px-3 lg:py-0">
          <span className="flex size-7 items-center justify-center rounded-full border bg-popover text-muted-foreground">
            <ArrowRight aria-hidden className="size-3.5 rotate-90 lg:rotate-0" />
          </span>
        </div>

        <div className="flex min-w-0 flex-col">
          <PaneHeader source="Anki" name="Study notes::Reading" />
          <div className={cn(styles.cardStack, "flex-1 p-4")}>
            {CARDS.map((card) => (
              <div
                key={card.type}
                className={cn(styles.step, card.step, "flex flex-col rounded-xl border bg-card p-5")}
              >
                <Badge variant="secondary" className="self-start">
                  {card.type}
                </Badge>
                <p className="mt-4 text-base leading-snug font-medium text-card-foreground">
                  {card.question}
                </p>
                <hr className="my-4 border-border" />
                <p className="flex-1 text-sm leading-relaxed text-muted-foreground">{card.answer}</p>
                <p className="mt-4 font-mono text-xs text-muted-foreground">{card.tag}</p>
              </div>
            ))}
          </div>
        </div>
      </VisibleMotion>
      <figcaption className="mt-3 text-center text-xs text-muted-foreground">
        Sample content, drawn with the site&apos;s own components. Unticked rows get no card.
      </figcaption>
    </figure>
  );
}
