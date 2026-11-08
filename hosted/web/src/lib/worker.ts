import "server-only";

import { cookies, headers } from "next/headers";
import { NextResponse } from "next/server";

export const SESSION_COOKIE = "nas_session";
export const SESSION_MAX_AGE = 7 * 24 * 3600;

function workerConfig() {
  const url = process.env.WORKER_URL?.replace(/\/+$/, "");
  const secret = process.env.WORKER_API_SECRET;
  if (!url || !secret) {
    throw new Error("WORKER_URL and WORKER_API_SECRET must be set");
  }
  return { url, secret };
}

export function workerUrl(): string {
  return workerConfig().url;
}

/** The caller's address, as set by Vercel's edge. */
async function clientIp(): Promise<string> {
  const all = await headers();
  return (
    all.get("x-real-ip") ??
    all.get("x-forwarded-for")?.split(",")[0]?.trim() ??
    "unknown"
  );
}

/** Calls the worker with the shared secret, which never reaches browsers. */
export async function callWorker(
  path: string,
  init: { method: string; body?: string; session?: string },
): Promise<Response> {
  const { url, secret } = workerConfig();
  return fetch(`${url}${path}`, {
    method: init.method,
    body: init.body,
    cache: "no-store",
    headers: {
      Authorization: `Bearer ${secret}`,
      "Content-Type": "application/json",
      "X-Client-IP": await clientIp(),
      ...(init.session ? { "X-Session-Token": init.session } : {}),
    },
  });
}

export async function sessionToken(): Promise<string | undefined> {
  return (await cookies()).get(SESSION_COOKIE)?.value;
}

/** Same-origin check for state-changing requests, on top of SameSite. */
export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return false;
  try {
    return new URL(origin).host === request.headers.get("host");
  } catch {
    return false;
  }
}

export function forbidden(): NextResponse {
  return NextResponse.json(
    { error: "forbidden", message: "Not allowed." },
    { status: 403 },
  );
}

export function workerUnavailable(): NextResponse {
  return NextResponse.json(
    { error: "worker_unavailable", message: "The sync worker can't be reached." },
    { status: 502 },
  );
}
