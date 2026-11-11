"use client";

import { useState } from "react";

import { SettingsRow, SettingsSection } from "~/components/settings/settingsLayout";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Dialog,
  DialogDescription,
  DialogHeader,
  DialogPanel,
  DialogPopup,
  DialogTitle,
} from "~/components/ui/dialog";
import { Spinner } from "~/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "~/components/ui/table";
import { ApiFailure, type Backup, type Run, api, formatTime } from "~/lib/api";

type BadgeVariant = "success" | "warning" | "error" | "info" | "outline";

const STATUS_VIEW: Record<string, { label: string; variant: BadgeVariant }> = {
  running: { label: "Syncing", variant: "info" },
  ok: { label: "Synced", variant: "success" },
  warnings: { label: "Warnings", variant: "warning" },
  failed: { label: "Failed", variant: "error" },
  full_sync_needed: { label: "Full sync needed", variant: "warning" },
  auth_failed: { label: "Reconnect AnkiWeb", variant: "warning" },
  interrupted: { label: "Interrupted", variant: "outline" },
};

export function StatusBadge({ status, running }: { status: string; running?: boolean }) {
  const view = (running ? STATUS_VIEW.running : STATUS_VIEW[status]) ?? {
    label: status,
    variant: "outline" as const,
  };
  return <Badge variant={view.variant}>{view.label}</Badge>;
}

function changes(run: Run): string {
  const parts = (["added", "updated", "removed"] as const)
    .filter((key) => run.stats[key])
    .map((key) => `${run.stats[key]} ${key}`);
  return parts.join(", ") || "None";
}

function LogDialog({ run }: { run: Run }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        View log
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogPopup className="max-h-[80dvh] sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Sync log</DialogTitle>
            <DialogDescription>{formatTime(run.started_at)}</DialogDescription>
          </DialogHeader>
          <DialogPanel>
            <pre className="text-xs leading-relaxed whitespace-pre-wrap text-muted-foreground">
              {(run.log ?? [])
                .map((line) => `${new Date(line.at * 1000).toLocaleTimeString()}  ${line.text}`)
                .concat(run.warnings.map((warning) => `Warning: ${warning}`))
                .join("\n") || "Nothing logged."}
            </pre>
          </DialogPanel>
        </DialogPopup>
      </Dialog>
    </>
  );
}

export function LastRunRow({ run, running }: { run: Run | null; running: boolean }) {
  if (run === null) {
    return <SettingsRow title="Last sync" description="Nothing has run yet." />;
  }
  const latestLine = run.log?.[run.log.length - 1]?.text;
  return (
    <SettingsRow
      title="Last sync"
      description={running ? (latestLine ?? "Starting") : run.message || "Finished."}
      status={run.finished_at ? `Finished ${formatTime(run.finished_at)}` : undefined}
      control={<LogDialog run={run} />}
    />
  );
}

export function HistorySection({ runs }: { runs: Run[] }) {
  if (runs.length === 0) return null;
  return (
    <SettingsSection title="History">
      <div className="px-3 py-1 sm:px-4">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Started</TableHead>
              <TableHead>Changes</TableHead>
              <TableHead>Result</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((run) => (
              <TableRow key={run.id}>
                <TableCell>{formatTime(run.started_at)}</TableCell>
                <TableCell>{changes(run)}</TableCell>
                <TableCell>
                  <StatusBadge status={run.status} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </SettingsSection>
  );
}

function size(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function DownloadButton({
  name,
  onError,
}: {
  name: string;
  onError: (text: string) => void;
}) {
  const [busy, setBusy] = useState(false);

  async function download() {
    setBusy(true);
    try {
      const { url } = await api.backupLink(name);
      window.location.assign(url);
    } catch (failure) {
      onError(failure instanceof ApiFailure ? failure.message : "Could not get the backup.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button variant="outline" size="sm" disabled={busy} onClick={() => void download()}>
      {busy ? <Spinner /> : null}
      Download
    </Button>
  );
}

export function BackupsSection({
  backups,
  onError,
}: {
  backups: Backup[];
  onError: (text: string) => void;
}) {
  return (
    <SettingsSection title="Backups">
      {backups.length === 0 ? (
        <SettingsRow
          title="No backups yet"
          description="The worker saves your collection before each sync that changes it."
        />
      ) : (
        backups.map((backup) => (
          <SettingsRow
            key={backup.name}
            title={formatTime(backup.created_at)}
            description={`Your collection before this sync. ${size(backup.size)}. To restore, open it in Anki desktop with File > Import, then sync and choose upload.`}
            control={<DownloadButton name={backup.name} onError={onError} />}
          />
        ))
      )}
    </SettingsSection>
  );
}
