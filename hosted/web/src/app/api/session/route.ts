import { NextResponse } from "next/server";

import {
  SESSION_COOKIE,
  SESSION_MAX_AGE,
  callWorker,
  forbidden,
  sameOrigin,
  workerUnavailable,
} from "~/lib/worker";

export const dynamic = "force-dynamic";

/** Sign in: exchanges the owner password for an HttpOnly session cookie. */
export async function POST(request: Request) {
  if (!sameOrigin(request)) return forbidden();
  let upstream: Response;
  try {
    upstream = await callWorker("/session", {
      method: "POST",
      body: await request.text(),
    });
  } catch {
    return workerUnavailable();
  }
  const payload = await upstream.json().catch(() => null);
  if (payload === null) return workerUnavailable();
  if (!upstream.ok) {
    return NextResponse.json(payload, { status: upstream.status });
  }
  const response = NextResponse.json({ ok: true });
  response.cookies.set(SESSION_COOKIE, payload.token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    // Lax so the redirect back from Notion arrives signed in; every
    // state-changing route also checks the Origin header
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE,
  });
  return response;
}

/** Sign out. */
export async function DELETE(request: Request) {
  if (!sameOrigin(request)) return forbidden();
  const response = NextResponse.json({ ok: true });
  response.cookies.delete(SESSION_COOKIE);
  return response;
}
