"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ConnectAnkiWebDialog, ConnectNotionDialog } from "~/components/ConnectDialogs";
import { BackupsSection, HistorySection, LastRunRow, StatusBadge } from "~/components/RunViews";
import { SettingsRow, SettingsSection } from "~/components/settings/settingsLayout";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import {
  AlertDialog,
  AlertDialogClose,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogPopup,
  AlertDialogTitle,
} from "~/components/ui/alert-dialog";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Select,
  SelectItem,
  SelectPopup,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";
import { ApiFailure, type Status, api, formatTime } from "~/lib/api";
import { type Theme, applyTheme, currentTheme } from "~/lib/theme";

const POLL_RUNNING_MS = 2500;
const POLL_IDLE_MS = 20000;

const SCHEDULE_LABELS: Record<string, string> = {
  "0": "Manual only",
  "15": "Every 15 minutes",
  "30": "Every 30 minutes",
  "60": "Every hour",
  "180": "Every 3 hours",
  "360": "Every 6 hours",
  "720": "Every 12 hours",
  "1440": "Every day",
};

const THEME_LABELS: Record<Theme, string> = { light: "Light", ocean: "Ocean light" };

function message(failure: unknown, fallback: string): string {
  return failure instanceof ApiFailure ? failure.message : fallback;
}

function ConnectionBadge({ connected }: { connected: boolean }) {
  return connected ? (
    <Badge variant="success">Connected</Badge>
  ) : (
    <Badge variant="outline">Not connected</Badge>
  );
}

function PagesRow({
  status,
  onSaved,
  onError,
}: {
  status: Status;
  onSaved: () => void;
  onError: (text: string) => void;
}) {
  const saved = status.pages.map((page) => `https://www.notion.so/${page.id}`).join("\n");
  const [text, setText] = useState(saved);
  const [busy, setBusy] = useState(false);
  const lastSaved = useRef(saved);

  useEffect(() => {
    // keep what the user is typing; take server changes otherwise
    setText((current) => (current === lastSaved.current ? saved : current));
    lastSaved.current = saved;
  }, [saved]);

  async function save() {
    setBusy(true);
    try {
      await api.savePages(text.split("\n"));
      onSaved();
    } catch (failure) {
      onError(message(failure, "Could not save the pages."));
    } finally {
      setBusy(false);
    }
  }

  const titles = status.pages.filter((page) => page.title).map((page) => page.title);
  return (
    <SettingsRow
      title="Page links"
      description="One Notion page link per line. Share each page with your integration first."
      status={titles.length ? titles.join(", ") : undefined}
      control={
        <Button
          variant="outline"
          size="sm"
          disabled={busy || text === saved}
          onClick={() => void save()}
        >
          {busy ? <Spinner /> : null}
          Save
        </Button>
      }
    >
      <div className="pt-2 pb-3">
        <Textarea
          aria-label="Notion page links"
          rows={4}
          value={text}
          placeholder="https://www.notion.so/My-Page-0123456789abcdef0123456789abcdef"
          onChange={(event) => setText(event.target.value)}
        />
      </div>
    </SettingsRow>
  );
}

function DisconnectRow({ onDone, onError }: { onDone: () => void; onError: (text: string) => void }) {
  const [open, setOpen] = useState(false);

  async function confirm() {
    try {
      await api.disconnect();
      onDone();
    } catch (failure) {
      onError(message(failure, "Could not disconnect."));
    } finally {
      setOpen(false);
    }
  }

  return (
    <SettingsRow
      title="Forget credentials"
      description="Deletes the AnkiWeb host key and the Notion token from the worker."
      control={
        <>
          <Button variant="destructive-outline" size="sm" onClick={() => setOpen(true)}>
            Forget
          </Button>
          <AlertDialog open={open} onOpenChange={setOpen}>
            <AlertDialogPopup>
              <AlertDialogHeader>
                <AlertDialogTitle>Forget these credentials?</AlertDialogTitle>
                <AlertDialogDescription>
                  Syncing stops until you connect AnkiWeb and Notion again. Your cards are not
                  changed.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogClose render={<Button variant="outline" />}>Cancel</AlertDialogClose>
                <Button variant="destructive" onClick={() => void confirm()}>
                  Forget
                </Button>
              </AlertDialogFooter>
            </AlertDialogPopup>
          </AlertDialog>
        </>
      }
    />
  );
}

export function Dashboard() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [theme, setTheme] = useState<Theme>("light");
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const refresh = useCallback(async () => {
    clearTimeout(timer.current);
    let running = false;
    try {
      const next = await api.status();
      setStatus(next);
      running = next.running;
    } catch (failure) {
      if (failure instanceof ApiFailure && failure.status === 401) {
        window.location.assign("/login");
        return;
      }
      setError(message(failure, "Could not load the status."));
    }
    timer.current = setTimeout(() => void refresh(), running ? POLL_RUNNING_MS : POLL_IDLE_MS);
  }, []);

  useEffect(() => {
    setTheme(currentTheme());
    void refresh();
    return () => clearTimeout(timer.current);
  }, [refresh]);

  async function startSync(full = false) {
    setStarting(true);
    setError(null);
    try {
      await api.sync(full);
    } catch (failure) {
      setError(message(failure, "Could not start the sync."));
    } finally {
      setStarting(false);
      void refresh();
    }
  }

  async function saveSchedule(value: string) {
    setError(null);
    try {
      await api.saveSchedule(Number(value));
    } catch (failure) {
      setError(message(failure, "Could not save the schedule."));
    } finally {
      void refresh();
    }
  }

  async function signOut() {
    await api.logout().catch(() => undefined);
    window.location.assign("/login");
  }

  const running = status?.running ?? false;

  return (
    <div className="flex h-svh flex-col bg-background text-foreground">
      <header className="flex h-(--workspace-topbar-height) shrink-0 items-center justify-between gap-3 border-b border-border px-4 sm:px-6">
        <div className="flex min-w-0 items-center gap-2">
          <h1 className="truncate text-sm font-semibold">Notion Sync</h1>
          {status?.latest ? <StatusBadge status={status.latest.status} running={running} /> : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button variant="ghost-muted" size="sm" onClick={() => void signOut()}>
            Sign out
          </Button>
          <Button size="sm" disabled={!status || running || starting} onClick={() => void startSync()}>
            {running || starting ? <Spinner /> : null}
            {running ? "Syncing" : "Sync now"}
          </Button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-5 pt-6 pb-12 sm:px-6">
          {status?.needed_action === "full_sync" ? (
            <Alert variant="warning">
              <AlertTitle>Full sync needed</AlertTitle>
              <AlertDescription>
                AnkiWeb asked for a full sync, so nothing was changed and scheduled syncs are
                paused. Sync from Anki desktop and choose which side to keep, then press Sync now.
              </AlertDescription>
            </Alert>
          ) : null}
          {status?.needed_action === "reconnect_ankiweb" ? (
            <Alert variant="warning">
              <AlertTitle>Reconnect AnkiWeb</AlertTitle>
              <AlertDescription>
                AnkiWeb no longer accepts this login. Scheduled syncs are paused until you
                connect it again.
              </AlertDescription>
            </Alert>
          ) : null}
          {error ? (
            <Alert variant="error">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          {status === null ? (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-16 rounded-xl" />
              <Skeleton className="h-40 rounded-xl" />
            </div>
          ) : (
            <>
              <SettingsSection title="Status">
                <LastRunRow run={status.latest} running={running} />
                <SettingsRow
                  title="Next scheduled sync"
                  description={
                    status.next_run_at
                      ? formatTime(status.next_run_at)
                      : status.schedule_minutes
                        ? "Paused. See above, or finish connecting."
                        : "No schedule set."
                  }
                />
                <SettingsRow
                  title="Full resync"
                  description="Downloads every row from Notion again. Not usually needed."
                  control={
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={running || starting}
                      onClick={() => void startSync(true)}
                    >
                      Run full resync
                    </Button>
                  }
                />
              </SettingsSection>

              <SettingsSection title="Connections">
                <SettingsRow
                  title="AnkiWeb"
                  description="Your password is used once to get a host key, and never stored."
                  control={
                    <>
                      <ConnectionBadge connected={status.ankiweb_connected} />
                      <ConnectAnkiWebDialog
                        connected={status.ankiweb_connected}
                        onDone={() => void refresh()}
                      />
                    </>
                  }
                />
                <SettingsRow
                  title="Notion"
                  description="Internal integration token, stored encrypted on the worker."
                  control={
                    <>
                      <ConnectionBadge connected={status.notion_connected} />
                      <ConnectNotionDialog
                        connected={status.notion_connected}
                        onDone={() => void refresh()}
                      />
                    </>
                  }
                />
                <DisconnectRow onDone={() => void refresh()} onError={setError} />
              </SettingsSection>

              <SettingsSection title="Notion pages">
                <PagesRow
                  status={status}
                  onSaved={() => void refresh()}
                  onError={setError}
                />
              </SettingsSection>

              <SettingsSection title="Schedule">
                <SettingsRow
                  title="Sync automatically"
                  description="Runs without Anki open. While the worker sleeps, a due sync waits until you next open this site."
                  control={
                    <Select
                      value={String(status.schedule_minutes)}
                      onValueChange={(value) => void saveSchedule(String(value))}
                    >
                      <SelectTrigger size="sm" className="w-full sm:w-56" aria-label="Schedule">
                        <SelectValue>{SCHEDULE_LABELS[String(status.schedule_minutes)]}</SelectValue>
                      </SelectTrigger>
                      <SelectPopup align="end" alignItemWithTrigger={false}>
                        {Object.entries(SCHEDULE_LABELS).map(([value, label]) => (
                          <SelectItem key={value} value={value}>
                            {label}
                          </SelectItem>
                        ))}
                      </SelectPopup>
                    </Select>
                  }
                />
              </SettingsSection>

              <HistorySection runs={status.runs} />
              <BackupsSection backups={status.backups} onError={setError} />

              <SettingsSection title="Appearance">
                <SettingsRow
                  title="Theme"
                  control={
                    <Select
                      value={theme}
                      onValueChange={(value) => {
                        const next = value as Theme;
                        setTheme(next);
                        applyTheme(next);
                      }}
                    >
                      <SelectTrigger size="sm" className="w-full sm:w-56" aria-label="Theme">
                        <SelectValue>{THEME_LABELS[theme]}</SelectValue>
                      </SelectTrigger>
                      <SelectPopup align="end" alignItemWithTrigger={false}>
                        <SelectItem value="light">Light</SelectItem>
                        <SelectItem value="ocean">Ocean light</SelectItem>
                      </SelectPopup>
                    </Select>
                  }
                />
              </SettingsSection>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
