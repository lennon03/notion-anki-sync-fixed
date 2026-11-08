import { NextResponse } from "next/server";

import { callWorker, sessionToken } from "~/lib/worker";

export const dynamic = "force-dynamic";

/** Sends the signed-in owner to Notion's consent page. */
export async function GET(request: Request) {
  const session = await sessionToken();
  if (!session) return NextResponse.redirect(new URL("/login", request.url));
  try {
    const upstream = await callWorker("/notion/oauth/start", { method: "POST", session });
    const payload = await upstream.json();
    if (upstream.ok && typeof payload?.url === "string") {
      return NextResponse.redirect(payload.url);
    }
  } catch {
    // reported below
  }
  return NextResponse.redirect(new URL("/?notion=failed", request.url));
}
