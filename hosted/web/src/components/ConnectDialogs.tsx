"use client";

import { type FormEvent, type ReactNode, useState } from "react";

import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogPanel,
  DialogPopup,
  DialogTitle,
} from "~/components/ui/dialog";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Spinner } from "~/components/ui/spinner";
import { ApiFailure, api } from "~/lib/api";

function ConnectDialog({
  connected,
  title,
  description,
  canSubmit,
  submit,
  reset,
  onDone,
  children,
}: {
  connected: boolean;
  title: string;
  description: string;
  canSubmit: boolean;
  submit: () => Promise<unknown>;
  reset: () => void;
  onDone: () => void;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function change(next: boolean) {
    setOpen(next);
    if (!next) {
      reset(); // don't keep secrets in memory once the dialog is closed
      setError(null);
    }
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await submit();
      change(false);
      onDone();
    } catch (failure) {
      setError(failure instanceof ApiFailure ? failure.message : "Could not connect.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        {connected ? "Reconnect" : "Connect"}
      </Button>
      <Dialog open={open} onOpenChange={change}>
        <DialogPopup className="sm:max-w-md">
          <form className="contents" onSubmit={onSubmit}>
            <DialogHeader>
              <DialogTitle>{title}</DialogTitle>
              <DialogDescription>{description}</DialogDescription>
            </DialogHeader>
            <DialogPanel className="flex flex-col gap-4">
              {children}
              {error ? (
                <Alert variant="error">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              ) : null}
            </DialogPanel>
            <DialogFooter>
              <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
              <Button type="submit" disabled={busy || !canSubmit}>
                {busy ? <Spinner /> : null}
                Connect
              </Button>
            </DialogFooter>
          </form>
        </DialogPopup>
      </Dialog>
    </>
  );
}

export function ConnectAnkiWebDialog({
  connected,
  onDone,
}: {
  connected: boolean;
  onDone: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  return (
    <ConnectDialog
      connected={connected}
      title="Connect AnkiWeb"
      description="Your password is sent once to get a host key from AnkiWeb. It isn't stored."
      canSubmit={Boolean(username && password)}
      submit={() => api.connectAnkiWeb(username, password)}
      reset={() => {
        setUsername("");
        setPassword("");
      }}
      onDone={onDone}
    >
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="ankiweb-id">AnkiWeb ID</Label>
        <Input
          id="ankiweb-id"
          type="email"
          autoComplete="username"
          required
          value={username}
          onChange={(event) => setUsername(event.target.value)}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="ankiweb-password">Password</Label>
        <Input
          id="ankiweb-password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </div>
    </ConnectDialog>
  );
}

export function ConnectNotionDialog({
  connected,
  onDone,
}: {
  connected: boolean;
  onDone: () => void;
}) {
  const [token, setToken] = useState("");

  return (
    <ConnectDialog
      connected={connected}
      title="Connect Notion"
      description="Paste the internal integration token. It's stored encrypted and never shown again."
      canSubmit={Boolean(token.trim())}
      submit={() => api.connectNotion(token)}
      reset={() => setToken("")}
      onDone={onDone}
    >
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="notion-token">Integration token</Label>
        <Input
          id="notion-token"
          type="password"
          autoComplete="off"
          required
          value={token}
          onChange={(event) => setToken(event.target.value)}
        />
      </div>
    </ConnectDialog>
  );
}
