"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { SettingsRow } from "~/components/settings/settingsLayout";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";
import { ApiFailure, type NotionPage, type Page, api } from "~/lib/api";

const MAX_INDENT = 4;

/** Page id -> how many listed ancestors it has. */
function depths(pages: NotionPage[]): Map<string, number> {
  const parents = new Map(pages.map((page) => [page.id, page.parent]));
  const out = new Map<string, number>();
  for (const page of pages) {
    let depth = 0;
    let parent = page.parent;
    while (parent && parents.has(parent) && depth < MAX_INDENT) {
      depth += 1;
      parent = parents.get(parent) ?? "";
    }
    out.set(page.id, depth);
  }
  return out;
}

function sameSet(a: Set<string>, b: Set<string>): boolean {
  return a.size === b.size && [...a].every((value) => b.has(value));
}

export function PagePicker({
  connected,
  canSignIn,
  saved,
  onSaved,
  onError,
}: {
  connected: boolean;
  canSignIn: boolean;
  saved: Page[];
  onSaved: () => void;
  onError: (text: string) => void;
}) {
  const savedKey = saved.map((page) => page.id).join(",");
  const savedIds = useMemo(() => new Set(savedKey ? savedKey.split(",") : []), [savedKey]);
  const [pages, setPages] = useState<NotionPage[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(savedIds);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => setSelected(savedIds), [savedIds]);

  const load = useCallback(async () => {
    setPages(null);
    setTruncated(false);
    try {
      const found = await api.notionPages();
      setPages(found.pages);
      setTruncated(Boolean(found.truncated));
    } catch (failure) {
      setPages([]);
      onError(failure instanceof ApiFailure ? failure.message : "Could not list your pages.");
    }
  }, [onError]);

  useEffect(() => {
    if (connected) void load();
  }, [connected, load]);

  async function save(ids: string[]) {
    setBusy(true);
    try {
      await api.savePages(ids);
      onSaved();
    } catch (failure) {
      onError(failure instanceof ApiFailure ? failure.message : "Could not save the pages.");
    } finally {
      setBusy(false);
    }
  }

  function toggle(id: string, on: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  const listed: NotionPage[] = useMemo(() => {
    const known = new Set((pages ?? []).map((page) => page.id));
    const missing = saved
      .filter((page) => !known.has(page.id))
      .map((page) => ({ id: page.id, title: page.title || page.id, parent: "" }));
    return [...missing, ...(pages ?? [])];
  }, [pages, saved]);

  const levels = useMemo(() => depths(listed), [listed]);
  const query = filter.trim().toLowerCase();
  const shown = query
    ? listed.filter((page) => page.title.toLowerCase().includes(query))
    : listed;

  if (!connected) {
    return (
      <SettingsRow title="Pages" description="Connect Notion to choose which pages to sync." />
    );
  }

  return (
    <>
      <SettingsRow
        title="Pages to sync"
        description="Turning a page off stops syncing it and keeps its cards. Child pages have their own switch."
        control={
          <>
            <Button variant="ghost-muted" size="sm" onClick={() => void load()}>
              Refresh
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={busy || sameSet(selected, savedIds)}
              onClick={() => void save([...selected])}
            >
              {busy ? <Spinner /> : null}
              Save
            </Button>
          </>
        }
      >
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
            <p className="px-3 py-3 text-xs text-muted-foreground sm:px-4">
              No pages found. Use Share more pages below to give access to one.
            </p>
          ) : (
            shown.map((page) => (
              <div
                key={page.id}
                className="flex items-center justify-between gap-4 px-3 py-2 sm:px-4"
                style={{ paddingInlineStart: query ? undefined : `${(levels.get(page.id) ?? 0) + 1}rem` }}
              >
                <span className="min-w-0 truncate text-sm text-foreground">{page.title}</span>
                <Switch
                  aria-label={`Sync ${page.title}`}
                  checked={selected.has(page.id)}
                  onCheckedChange={(on) => toggle(page.id, on)}
                />
              </div>
            ))
          )}
          {truncated ? (
            <p className="px-3 py-3 text-xs text-muted-foreground sm:px-4">
              Notion has more pages and table rows than this list reads, so some pages may be
              missing.
            </p>
          ) : null}
        </div>
      )}
      {canSignIn ? (
        <SettingsRow
          title="Need another page or table?"
          description="Opens Notion's page picker so you can share more. For a table, choose the page it sits on."
          control={
            <Button variant="outline" size="sm" render={<a href="/api/notion/start" />}>
              Share more pages
            </Button>
          }
        />
      ) : null}
    </>
  );
}
