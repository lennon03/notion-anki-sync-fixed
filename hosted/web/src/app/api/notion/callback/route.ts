import { NextResponse } from "next/server";

import { callWorker, sessionToken } from "~/lib/worker";

export const dynamic = "force-dynamic";

/** Where Notion sends the owner back; the worker checks the state and code. */
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const code = searchParams.get("code");
  const state = searchParams.get("state");
  const session = await sessionToken();
  if (!session) return NextResponse.redirect(new URL("/login", request.url));
  let result = "failed";
  if (code && state) {
    try {
      const upstream = await callWorker("/notion/oauth/finish", {
        method: "POST",
        session,
        body: JSON.stringify({ code, state }),
      });
      if (upstream.ok) result = "connected";
    } catch {
      // reported below
    }
  }
  return NextResponse.redirect(new URL(`/?notion=${result}`, request.url));
}
