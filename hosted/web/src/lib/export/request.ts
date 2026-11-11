import { type ExportTable, TEXT_SIZES, type TextSize } from "./types";

export const MAX_TABLES = 12;
const MAX_NAME = 200;

/** A Notion id, with or without dashes. Anything else never reaches the worker. */
export const NOTION_ID = /^(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$/i;

export function plainId(value: string): string {
  return value.replace(/-/g, "").toLowerCase();
}

export function isNotionId(value: unknown): value is string {
  return typeof value === "string" && NOTION_ID.test(value);
}

/** Checks `{id, name}` pairs from a browser; null when any is not valid. */
export function parseTables(raw: unknown): ExportTable[] | null {
  if (!Array.isArray(raw) || raw.length === 0 || raw.length > MAX_TABLES) return null;
  const tables: ExportTable[] = [];
  for (const item of raw) {
    if (!isNotionId(item?.id)) return null;
    const name = typeof item?.name === "string" ? item.name.trim().slice(0, MAX_NAME) : "";
    tables.push({ id: plainId(item.id), name: name || "Notion table" });
  }
  return tables;
}

export function parseSize(value: unknown): TextSize {
  return TEXT_SIZES.find((size) => size === value) ?? "medium";
}

/** A plain ASCII file name from a title. */
export function fileName(name: string, extension: string): string {
  const base = name
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-zA-Z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  return `${base || "export"}.${extension}`;
}
