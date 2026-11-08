/** The parts of Notion's JSON the exporters read. Everything is optional: Notion adds fields. */

export interface RichText {
  type?: string;
  plain_text?: string;
  text?: { content?: string };
  equation?: { expression?: string };
  annotations?: {
    bold?: boolean;
    italic?: boolean;
    strikethrough?: boolean;
    underline?: boolean;
    code?: boolean;
    color?: string;
  };
}

export interface BlockValue {
  rich_text?: RichText[];
  caption?: RichText[];
  language?: string;
  checked?: boolean;
  expression?: string;
  type?: string;
  external?: { url?: string };
  file?: { url?: string };
  cells?: RichText[][];
  has_column_header?: boolean;
}

export interface NotionBlock {
  id: string;
  type: string;
  children?: NotionBlock[];
  [key: string]: unknown;
}

export function valueOf(block: NotionBlock): BlockValue {
  return (block[block.type] ?? {}) as BlockValue;
}

export type TextSize = "small" | "medium" | "large";
export const TEXT_SIZES: readonly TextSize[] = ["small", "medium", "large"];

export interface ExportTable {
  id: string;
  name: string;
}

export interface ExportRow {
  id: string;
  title: string;
}

/** What the exporters need from Notion. The worker implements it; nothing here holds a token. */
export interface NotionSource {
  rows(tableId: string): Promise<ExportRow[]>;
  blocks(rowId: string): Promise<NotionBlock[]>;
}
