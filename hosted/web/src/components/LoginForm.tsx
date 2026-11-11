"use client";

import { type FormEvent, useState } from "react";

import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Spinner } from "~/components/ui/spinner";
import { ApiFailure, api } from "~/lib/api";

/** The card markup is T3 Code's StandalonePage. */
export function LoginForm() {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(password);
      window.location.assign("/");
    } catch (failure) {
      setPassword("");
      setError(failure instanceof ApiFailure ? failure.message : "Sign-in failed.");
      setBusy(false);
    }
  }

  return (
    <div className="flex h-svh items-center-safe justify-center overflow-y-auto bg-background px-4 py-10 text-foreground sm:px-6">
      <section className="dialog-glass w-full max-w-xl rounded-2xl border p-6">
        <div className="flex items-baseline justify-between gap-3 text-muted-foreground">
          <span className="text-lg font-medium tracking-tight text-foreground">Notion Sync</span>
        </div>
        <h1 className="mt-6 text-xl leading-tight font-semibold">Sign in</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Enter the owner password to manage your sync.
        </p>
        <form className="mt-6 flex flex-col gap-4" onSubmit={submit}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              autoFocus
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          {error ? (
            <Alert variant="error">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <Button type="submit" disabled={busy || !password}>
            {busy ? <Spinner /> : null}
            Sign in
          </Button>
        </form>
      </section>
    </div>
  );
}
