import { ArrowRight } from "lucide-react";
import type { ReactNode } from "react";

import { VisibleMotion } from "~/components/landing/VisibleMotion";
import { Badge } from "~/components/ui/badge";
import { cn } from "~/lib/utils";

import styles from "./landing.module.css";

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
    type: "List",
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

function Syntax({ children }: { children: ReactNode }) {
  return <span className="font-mono text-muted-foreground/70">{children}</span>;
}

/** A Notion page on one side, the cards it makes on the other. Sample content. */
export function HeroDemo() {
  return (
    <figure className="m-0">
      <VisibleMotion className="dialog-glass grid overflow-hidden rounded-2xl border text-left lg:grid-cols-[minmax(0,1.3fr)_auto_minmax(0,1fr)]">
        <div className="min-w-0">
          <PaneHeader source="Notion" name="Study notes / Reading" />
          <div className="space-y-1 p-3 text-[0.8125rem] leading-relaxed sm:p-4 sm:text-sm">
            <p className="px-2 pb-1 text-lg font-semibold tracking-tight text-foreground">Reading</p>
            <div className={cn(styles.row, styles.rowOne, "rounded-lg px-2 py-1.5")}>
              <p className="font-semibold text-foreground">
                <Syntax>### </Syntax>What does the categorical imperative ask?
              </p>
              <p className="text-muted-foreground">
                Act only on a maxim you could will to be a universal law.{" "}
                <s className="text-muted-foreground/70">ethics</s>
              </p>
            </div>
            <div className={cn(styles.row, styles.rowTwo, "rounded-lg px-2 py-1.5")}>
              <p className="text-foreground">
                The capital of Australia is <Syntax>{"{{"}</Syntax>Canberra<Syntax>{"}}"}</Syntax>.{" "}
                <s className="text-muted-foreground/70">geography</s>
              </p>
            </div>
            <div className={cn(styles.row, styles.rowThree, "rounded-lg px-2 py-1.5")}>
              <p className="font-semibold text-foreground">
                <Syntax>### </Syntax>Ozymandias, opening lines
              </p>
              <ol className="list-decimal pl-5 text-muted-foreground">
                <li>I met a traveller from an antique land,</li>
                <li>Who said: two vast and trunkless legs of stone</li>
              </ol>
            </div>
            <p className="px-2 py-1.5 text-muted-foreground/80">
              Ideas to sort later. Ordinary notes like this one stay notes.
            </p>
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
        Sample content, drawn with the site&apos;s own components. Text that isn&apos;t a card
        gets no card.
      </figcaption>
    </figure>
  );
}
