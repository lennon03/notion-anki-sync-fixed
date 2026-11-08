import { NextResponse } from "next/server";

import {
  callWorker,
  forbidden,
  sameOrigin,
  sessionToken,
  workerUnavailable,
  workerUrl,
} from "~/lib/worker";

export const dynamic = "force-dynamic";

/** What the browser may reach on the worker, and with which method. */
function allowed(method: string, parts: string[]): boolean {
  const path = parts.join("/");
  if (method === "GET") {
    return (
      path === "status" ||
      path === "notion/pages" ||
      /^notion\/export\/page\/[0-9a-fA-F-]{32,36}$/.test(path)
    );
  }
  if (method === "POST") {
    return (
      path === "connect" ||
      path === "sync" ||
      /^backups\/[A-Za-z0-9._-]+\/link$/.test(path)
    );
  }
  if (method === "PUT") return path === "config";
  if (method === "DELETE") return path === "connect";
  return false;
}

async function proxy(request: Request, parts: string[]) {
  const method = request.method;
  if (!allowed(method, parts)) {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }
  if (method !== "GET" && !sameOrigin(request)) return forbidden();
  const session = await sessionToken();
  if (!session) {
    return NextResponse.json(
      { error: "no_session", message: "Sign in first." },
      { status: 401 },
    );
  }
  let upstream: Response;
  try {
    upstream = await callWorker(`/${parts.join("/")}`, {
      method,
      session,
      body: method === "GET" ? undefined : await request.text(),
    });
  } catch {
    return workerUnavailable();
  }
  const payload = await upstream.json();
  // The browser follows backup links straight to the worker: a function
  // here can't stream a whole collection.
  if (upstream.ok && typeof payload?.path === "string") {
    return NextResponse.json({ url: `${workerUrl()}${payload.path}` });
  }
  return NextResponse.json(payload, { status: upstream.status });
}

type Context = { params: Promise<{ path: string[] }> };

async function handle(request: Request, context: Context) {
  const { path } = await context.params;
  return proxy(request, path);
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const DELETE = handle;
