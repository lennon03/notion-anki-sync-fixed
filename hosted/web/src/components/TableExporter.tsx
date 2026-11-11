"use client";

import { type FormEvent, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";

import { SettingsRow, SettingsSection } from "~/components/settings/settingsLayout";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import {
  Select,
  SelectItem,
  SelectPopup,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";
import {
  ApiFailure,
  type ExportPage,
  type NotionPage,
  type Status,
  api,
} from "~/lib/api";
import { MAX_TABLES, isNotionId } from "~/lib/export/request";

const FORMATS = { pdf: "PDF", docx: "Word", html: "HTML" } as const;
type Format = keyof typeof FORMATS;
const SIZES = { small: "Small", medium: "Medium", large: "Large" } as const;
type Size = keyof typeof SIZES;

function message(failure: unknown, fallback: string): string {
  return failure instanceof ApiFailure ? failure.message : fallback;
}

/** Sends a signed-out browser to the sign-in page; returns true when it did. */
function signedOut(failure: unknown): boolean {
  if (failure instanceof ApiFailure && failure.status === 401) {
    window.location.assign("/login");
    return true;
  }
  return false;
}

function parsePageId(input: string): string | null {
  const value = input.trim();
  if (isNotionId(value)) return value;
  try {
    return (
      new URL(value).pathname.match(/([0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$/i)?.[1] ??
      null
    );
  } catch {
    return null;
  }
}

// Recently opened pages: ids and names only, kept in this browser as a convenience.
const RECENTS_KEY = "export-recents";
interface Recent {
  id: string;
  name: string;
  at: number;
}

function loadRecents(): Recent[] {
  try {
    const items: unknown = JSON.parse(localStorage.getItem(RECENTS_KEY) || "[]");
    return Array.isArray(items)
      ? items.filter(
          (item): item is Recent => typeof item?.id === "string" && typeof item?.name === "string",
        )
      : [];
  } catch {
    return [];
  }
}

function storeRecents(items: Recent[]) {
  try {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(items));
  } catch {
    // private mode or blocked storage: the list is only a convenience
  }
}

function rememberPage(id: string, name: string) {
  storeRecents(
    [{ id, name, at: Date.now() }, ...loadRecents().filter((item) => item.id !== id)].slice(0, 8),
  );
}

function Shell({ actions, children }: { actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex h-svh flex-col bg-background text-foreground">
      <header className="flex h-(--workspace-topbar-height) shrink-0 items-center justify-between gap-3 border-b border-border px-4 sm:px-6">
        <div className="flex min-w-0 items-center gap-2">
          <a
            href="/"
            className="shrink-0 text-sm text-muted-foreground hover:text-foreground"
          >
            Notion Sync
          </a>
          <span className="text-sm text-muted-foreground" aria-hidden="true">
            /
          </span>
          <h1 className="truncate text-sm font-semibold">Table Exporter</h1>
        </div>
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-5 pt-6 pb-12 sm:px-6">
          {children}
        </div>
      </div>
    </div>
  );
}

function ChoosePage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [pages, setPages] = useState<NotionPage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [link, setLink] = useState("");
  const [filter, setFilter] = useState("");
  const [recents, setRecents] = useState<Recent[]>([]);

  useEffect(() => {
    setRecents(loadRecents());
    void (async () => {
      try {
        const next = await api.status();
        setStatus(next);
        if (next.notion_connected) setPages((await api.notionPages()).pages);
        else setPages([]);
      } catch (failure) {
        if (signedOut(failure)) return;
        setPages([]);
        setError(message(failure, "Could not load your pages."));
      }
    })();
  }, []);

  function open(event: FormEvent) {
    event.preventDefault();
    const id = parsePageId(link);
    if (!id) {
      setError("That link doesn't contain a Notion page id.");
      return;
    }
    window.location.assign(`/export?id=${id}`);
  }

  function forget(id: string) {
    const next = recents.filter((item) => item.id !== id);
    storeRecents(next);
    setRecents(next);
  }

  const query = filter.trim().toLowerCase();
  const shown = (pages ?? []).filter((page) => page.title.toLowerCase().includes(query));

  return (
    <Shell>
      {error ? (
        <Alert variant="error">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {status && !status.notion_connected ? (
        <Alert variant="warning">
          <AlertDescription>
            Notion isn't connected yet. Sign in with Notion on the Notion Sync page, then come
            back.
          </AlertDescription>
        </Alert>
      ) : null}

      <SettingsSection title="Notion page">
        <SettingsRow
          title="Open a page"
          description="Paste the link of a page that has tables on it. The page has to be shared with Notion Sync."
        >
          <form className="flex gap-2 pt-2 pb-3" onSubmit={open}>
            <Input
              type="url"
              inputMode="url"
              aria-label="Notion page link"
              placeholder="https://www.notion.so/…"
              autoComplete="off"
              spellCheck={false}
              value={link}
              onChange={(event) => setLink(event.target.value)}
            />
            <Button type="submit" variant="outline" disabled={!link.trim()}>
              Open
            </Button>
          </form>
        </SettingsRow>
      </SettingsSection>

      {recents.length > 0 ? (
        <SettingsSection title="Recent pages">
          {recents.map((item) => (
            <SettingsRow
              key={item.id}
              title={item.name}
              control={
                <>
                  <Button variant="ghost-muted" size="sm" onClick={() => forget(item.id)}>
                    Remove
                  </Button>
                  <Button variant="outline" size="sm" render={<a href={`/export?id=${item.id}`} />}>
                    Open
                  </Button>
                </>
              }
            />
          ))}
        </SettingsSection>
      ) : null}

      {status?.notion_connected ? (
        <SettingsSection title="Shared pages">
          <SettingsRow title="Pages Notion Sync can read">
            <div className="pt-2 pb-3">
              <Input
                type="search"
                aria-label="Filter pages"
                placeholder="Filter pages"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
              />
            </div>
          </SettingsRow>
          {pages === null ? (
            <div className="flex flex-col gap-2 px-3 py-3 sm:px-4">
              <Skeleton className="h-5 rounded-md" />
              <Skeleton className="h-5 rounded-md" />
            </div>
          ) : (
            <div className="max-h-96 overflow-y-auto">
              {shown.length === 0 ? (
                <p className="px-3 py-3 text-xs text-muted-foreground sm:px-4">No pages found.</p>
              ) : (
                shown.map((page) => (
                  <div
                    key={page.id}
                    className="flex items-center justify-between gap-4 px-3 py-2 sm:px-4"
                  >
                    <span className="min-w-0 truncate text-sm text-foreground">{page.title}</span>
                    <Button
                      variant="outline"
                      size="sm"
                      aria-label={`Open ${page.title}`}
                      render={<a href={`/export?id=${page.id}`} />}
                    >
                      Open
                    </Button>
                  </div>
                ))
              )}
            </div>
          )}
        </SettingsSection>
      ) : null}
    </Shell>
  );
}

function ExportPageView({ pageId }: { pageId: string }) {
  const [page, setPage] = useState<ExportPage | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [format, setFormat] = useState<Format>("pdf");
  const [size, setSize] = useState<Size>("medium");
  const [working, setWorking] = useState(false);
  const [result, setResult] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const loaded = await api.exportPage(pageId);
        setPage(loaded);
        rememberPage(pageId, loaded.name);
      } catch (failure) {
        if (signedOut(failure)) return;
        setLoadError(message(failure, "Could not read this page."));
      }
    })();
  }, [pageId]);

  const tables = page?.tables ?? [];
  const chosen = useMemo(
    () => tables.filter((table) => selected.has(table.id)),
    [tables, selected],
  );
  const allSelected = tables.length > 0 && chosen.length === tables.length;

  function toggle(id: string, on: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  const run = useCallback(async () => {
    if (chosen.length === 0) {
      setResult({ kind: "error", text: "Choose at least one table." });
      return;
    }
    if (chosen.length > MAX_TABLES) {
      setResult({ kind: "error", text: `Choose at most ${MAX_TABLES} tables for one export.` });
      return;
    }
    setResult(null);
    if (format === "html") {
      // HTML isn't a file: the page is drawn live in a new tab.
      const params = new URLSearchParams({ size });
      for (const table of chosen) {
        params.append("t", table.id);
        params.append("n", table.name);
      }
      window.open(`/export/view?${params}`, "_blank", "noopener");
      setResult({ kind: "success", text: "The HTML page opens in a new tab." });
      return;
    }
    setWorking(true);
    try {
      const response = await fetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tables: chosen, format, size }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        if (response.status === 401) {
          window.location.assign("/login");
          return;
        }
        throw new Error(body?.message ?? `The ${FORMATS[format]} export failed.`);
      }
      const url = URL.createObjectURL(await response.blob());
      const name = (chosen.length === 1 ? chosen[0].name.trim() : "") || "export";
      Object.assign(document.createElement("a"), { href: url, download: `${name}.${format}` }).click();
      URL.revokeObjectURL(url);
      setResult({ kind: "success", text: `The ${FORMATS[format]} file is downloaded.` });
    } catch (failure) {
      setResult({
        kind: "error",
        text: failure instanceof Error ? failure.message : "The export failed.",
      });
    } finally {
      setWorking(false);
    }
  }, [chosen, format, size]);

  async function copyEmbed() {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}/embed?id=${pageId}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setResult({ kind: "error", text: "Could not copy the link." });
    }
  }

  return (
    <Shell
      actions={
        <>
          <Button variant="ghost-muted" size="sm" render={<a href="/export" />}>
            Change page
          </Button>
          <Button size="sm" disabled={!page || chosen.length === 0 || working} onClick={() => void run()}>
            {working ? <Spinner /> : null}
            {format === "html" ? "Open" : "Export"}
          </Button>
        </>
      }
    >
      {loadError ? (
        <Alert variant="error">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}
      {result ? (
        <Alert variant={result.kind}>
          <AlertDescription>{result.text}</AlertDescription>
        </Alert>
      ) : null}

      <SettingsSection
        title={page ? page.name : "Tables"}
        headerAction={
          tables.length > 0 ? (
            <Button
              variant="ghost-muted"
              size="sm"
              onClick={() => setSelected(allSelected ? new Set() : new Set(tables.map((t) => t.id)))}
            >
              {allSelected ? "Clear" : "Select all"}
            </Button>
          ) : null
        }
      >
        {page === null && !loadError ? (
          <div className="flex flex-col gap-2 px-3 py-3 sm:px-4">
            <Skeleton className="h-5 rounded-md" />
            <Skeleton className="h-5 rounded-md" />
          </div>
        ) : null}
        {page && tables.length === 0 ? (
          <SettingsRow
            title="No tables found"
            description="Use a page that has a table on it, or a table's own link. Both have to be shared with Notion Sync."
          />
        ) : null}
        {tables.map((table) => (
          <SettingsRow
            key={table.id}
            title={table.name}
            control={
              <Switch
                aria-label={`Export ${table.name}`}
                checked={selected.has(table.id)}
                onCheckedChange={(on) => toggle(table.id, on)}
              />
            }
          />
        ))}
      </SettingsSection>

      <SettingsSection title="Document">
        <SettingsRow
          title="Format"
          description="PDF and Word are files. HTML opens as a page you can print."
          control={
            <Select value={format} onValueChange={(value) => setFormat(value as Format)}>
              <SelectTrigger size="sm" className="w-full sm:w-56" aria-label="Format">
                <SelectValue>{FORMATS[format]}</SelectValue>
              </SelectTrigger>
              <SelectPopup align="end" alignItemWithTrigger={false}>
                {Object.entries(FORMATS).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectPopup>
            </Select>
          }
        />
        {format !== "docx" ? (
          <SettingsRow
            title="Text size"
            control={
              <Select value={size} onValueChange={(value) => setSize(value as Size)}>
                <SelectTrigger size="sm" className="w-full sm:w-56" aria-label="Text size">
                  <SelectValue>{SIZES[size]}</SelectValue>
                </SelectTrigger>
                <SelectPopup align="end" alignItemWithTrigger={false}>
                  {Object.entries(SIZES).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectPopup>
              </Select>
            }
          />
        ) : null}
      </SettingsSection>

      <SettingsSection title="Embed in Notion">
        <SettingsRow
          title="Launcher link"
          description="Paste it on a Notion page with /embed. Notion blocks downloads inside embeds, so the embed only shows a button that opens this exporter in a new tab, where you are signed in."
          control={
            <Button variant="outline" size="sm" onClick={() => void copyEmbed()}>
              {copied ? "Copied" : "Copy link"}
            </Button>
          }
        />
      </SettingsSection>
    </Shell>
  );
}

export function TableExporter({ pageId }: { pageId: string | null }) {
  return pageId ? <ExportPageView pageId={pageId} /> : <ChoosePage />;
}
