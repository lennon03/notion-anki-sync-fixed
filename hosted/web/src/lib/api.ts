export function formatTime(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export class ApiFailure extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export interface Page {
  id: string;
  title: string;
}

export interface LogLine {
  at: number;
  text: string;
}

export interface Run {
  id: number;
  trigger: string;
  status: string;
  started_at: number;
  finished_at: number | null;
  message: string;
  stats: Record<string, number>;
  warnings: string[];
  backup: string | null;
  log?: LogLine[];
}

export interface Backup {
  name: string;
  size: number;
  created_at: number;
}

export interface NotionPage {
  id: string;
  title: string;
  parent: string;
}

export interface Status {
  ankiweb_connected: boolean;
  notion_connected: boolean;
  notion_workspace: string | null;
  notion_oauth_available: boolean;
  pages: Page[];
  schedule_minutes: number;
  next_run_at: number | null;
  running: boolean;
  latest: Run | null;
  runs: Run[];
  backups: Backup[];
  needed_action: "full_sync" | "reconnect_ankiweb" | null;
}

async function request<T>(url: string, method: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiFailure("Network error.", "network", 0);
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiFailure(
      payload.message ?? "Request failed.",
      payload.error ?? "unknown",
      response.status,
    );
  }
  return payload as T;
}

export const api = {
  login: (password: string) => request("/api/session", "POST", { password }),
  logout: () => request("/api/session", "DELETE"),
  status: () => request<Status>("/api/worker/status", "GET"),
  connectAnkiWeb: (username: string, password: string) =>
    request("/api/worker/connect", "POST", { ankiweb: { username, password } }),
  disconnect: () => request("/api/worker/connect", "DELETE"),
  notionPages: () => request<{ pages: NotionPage[] }>("/api/worker/notion/pages", "GET"),
  savePages: (pages: string[]) => request("/api/worker/config", "PUT", { pages }),
  saveSchedule: (schedule_minutes: number) =>
    request("/api/worker/config", "PUT", { schedule_minutes }),
  sync: (full = false) => request("/api/worker/sync", "POST", { full }),
  backupLink: (name: string) =>
    request<{ url: string }>(`/api/worker/backups/${encodeURIComponent(name)}/link`, "POST"),
};
