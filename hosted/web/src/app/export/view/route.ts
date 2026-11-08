import { NextResponse } from "next/server";

import { escapeHtml, streamHtml } from "~/lib/export/html";
import { parseSize, parseTables } from "~/lib/export/request";
import { failure, workerSource } from "~/lib/export/source";
import { clientIp, sessionToken } from "~/lib/worker";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 60;

/**
 * The HTML export as a live page, with no file and no storage:
 * /export/view?size=medium&t=<table id>&n=<table name>[&t=...&n=...]
 * Its Content-Security-Policy (next.config.ts) forbids scripts and
 * lets it load images and nothing else.
 */
export async function GET(request: Request) {
  const session = await sessionToken();
  if (!session) return NextResponse.redirect(new URL("/login", request.url));

  const params = new URL(request.url).searchParams;
  const names = params.getAll("n");
  const tables = parseTables(
    params.getAll("t").map((id, index) => ({ id, name: names[index] ?? "" })),
  );
  if (!tables) return notice("Choose at least one table first.", 400);

  const stream = streamHtml(tables, workerSource(session, await clientIp()), {
    size: parseSize(params.get("size")),
    describe: (error) => failure(error).message,
  });
  const encoder = new TextEncoder();
  // The page shows at once and fills in as Notion answers.
  const body = new ReadableStream({
    async pull(controller) {
      const { value, done } = await stream.next();
      if (done) controller.close();
      else controller.enqueue(encoder.encode(value));
    },
    cancel() {
      void stream.return(undefined);
    },
  });
  return new Response(body, {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "private, no-store",
      "X-Accel-Buffering": "no",
    },
  });
}

function notice(message: string, status: number) {
  return new Response(
    `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Export unavailable</title><body style="font-family:system-ui,sans-serif;max-width:560px;margin:64px auto;padding:0 16px"><h1 style="font-size:20px;font-weight:600">Export unavailable</h1><p>${escapeHtml(message)}</p></body></html>`,
    { status, headers: { "Content-Type": "text/html; charset=utf-8" } },
  );
}
