import "server-only";

import { callWorker } from "~/lib/worker";

import type { ExportRow, NotionBlock, NotionSource } from "./types";

/** A failure with a message that is safe to show: the worker's fixed texts, never Notion's. */
export class ExportError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message);
  }
}

/** Most worker calls at once; each one fans out to Notion on its own. */
const WORKER_CONCURRENCY = 4;

function limiter(limit: number) {
  let active = 0;
  const waiting: Array<() => void> = [];
  return async <T>(task: () => Promise<T>): Promise<T> => {
    if (active < limit) active += 1;
    else await new Promise<void>((resume) => waiting.push(resume)); // the slot is handed over
    try {
      return await task();
    } finally {
      const next = waiting.shift();
      if (next) next();
      else active -= 1;
    }
  };
}

/**
 * Reads Notion through the worker, as the signed-in owner. The Notion token
 * lives only on the worker; this site gets the content, one request at a time.
 * `ip` is the browser's address, passed on for the worker's rate limits.
 */
export function workerSource(session: string, ip: string): NotionSource {
  const limit = limiter(WORKER_CONCURRENCY);

  async function read<T>(path: string): Promise<T> {
    let upstream: Response;
    try {
      upstream = await callWorker(path, { method: "GET", session, ip });
    } catch {
      throw new ExportError("The sync worker can't be reached.", 502, "worker_unavailable");
    }
    const payload = await upstream.json().catch(() => ({}));
    if (!upstream.ok) {
      throw new ExportError(
        typeof payload?.message === "string" ? payload.message : "Request failed.",
        upstream.status,
        typeof payload?.error === "string" ? payload.error : "unknown",
      );
    }
    return payload as T;
  }

  return {
    rows: async (tableId) =>
      (await read<{ rows: ExportRow[] }>(`/notion/export/table/${tableId}/rows`)).rows,
    blocks: (rowId) =>
      limit(async () =>
        (await read<{ blocks: NotionBlock[] }>(`/notion/export/blocks/${rowId}`)).blocks,
      ),
  };
}

/** What a failed export tells the browser. */
export function failure(error: unknown): { status: number; code: string; message: string } {
  if (error instanceof ExportError) {
    return { status: error.status, code: error.code, message: error.message };
  }
  console.error("Export failed", error instanceof Error ? error.name : "unknown");
  return { status: 500, code: "export_failed", message: "The export failed." };
}
