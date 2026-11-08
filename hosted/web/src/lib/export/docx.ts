import {
  BorderStyle,
  Document,
  HeadingLevel,
  Packer,
  Paragraph,
  type ParagraphChild,
  TextRun,
} from "docx";

import type { ExportTable, NotionBlock, NotionSource, RichText } from "./types";
import { valueOf } from "./types";

const COLORS: Record<string, string> = {
  gray: "787774",
  brown: "9F6B53",
  orange: "D9730D",
  yellow: "CB912F",
  green: "448361",
  blue: "337EA9",
  purple: "9065B0",
  pink: "C14C8A",
  red: "D44C47",
};
function color(value: string | undefined): string {
  return COLORS[value?.replace("_background", "") ?? ""] ?? "28303A";
}

function runs(richText: RichText[] = []): TextRun[] {
  return richText.map((item) => {
    const a = item.annotations ?? {};
    return new TextRun({
      text: item.plain_text ?? item.text?.content ?? "",
      bold: a.bold,
      italics: a.italic,
      strike: a.strikethrough,
      underline: a.underline ? {} : undefined,
      font: a.code ? "Consolas" : "Calibri",
      color: color(a.color),
      shading: a.color?.endsWith("_background") ? { fill: color(a.color) } : undefined,
    });
  });
}

function paragraphFor(block: NotionBlock, depth: number): Paragraph {
  const value = valueOf(block);
  const common = { children: runs(value.rich_text), spacing: { after: 80 }, keepLines: true };
  switch (block.type) {
    case "heading_1":
      return new Paragraph({ ...common, heading: HeadingLevel.HEADING_1, keepNext: true });
    case "heading_2":
      return new Paragraph({ ...common, heading: HeadingLevel.HEADING_2, keepNext: true });
    case "heading_3":
      return new Paragraph({ ...common, heading: HeadingLevel.HEADING_3, keepNext: true });
    case "bulleted_list_item":
      return new Paragraph({ ...common, bullet: { level: Math.min(depth, 8) } });
    case "numbered_list_item":
      return new Paragraph({
        ...common,
        children: [new TextRun({ text: `${"  ".repeat(depth)}• ` }), ...runs(value.rich_text)],
      });
    case "to_do":
      return new Paragraph({
        ...common,
        children: [new TextRun({ text: value.checked ? "☑ " : "☐ " }), ...runs(value.rich_text)],
      });
    case "quote":
      return new Paragraph({
        ...common,
        indent: { left: 500 },
        border: { left: { color: "D3D1CB", space: 8, style: BorderStyle.SINGLE, size: 14 } },
      });
    case "code":
      return new Paragraph({ ...common, shading: { fill: "F1F1EF" } });
    case "callout":
      return new Paragraph({
        ...common,
        children: [new TextRun({ text: "💡 " }), ...runs(value.rich_text)],
        shading: { fill: "F7F7F5" },
      });
    case "equation":
      return new Paragraph({
        children: [new TextRun({ text: value.expression ?? "", italics: true })],
        spacing: { after: 80 },
      });
    case "divider":
      return new Paragraph({
        border: { bottom: { color: "D3D1CB", space: 1, style: BorderStyle.SINGLE, size: 6 } },
      });
    case "table_row":
      return new Paragraph({
        children: (value.cells ?? []).flatMap((cell, index): ParagraphChild[] => [
          ...(index ? [new TextRun({ text: "\t" })] : []),
          ...runs(cell),
        ]),
        spacing: { after: 80 },
      });
    default:
      return new Paragraph(common);
  }
}

function paragraphsOf(blocks: NotionBlock[], depth = 0): Paragraph[] {
  return blocks.flatMap((block) => [
    paragraphFor(block, depth),
    ...paragraphsOf(block.children ?? [], depth + 1),
  ]);
}

/** A Word document with Notion's text styling and main block structure. */
export async function exportDocx(
  tables: ExportTable[],
  source: NotionSource,
): Promise<Buffer> {
  const children: Paragraph[] = [];
  for (const table of tables) {
    children.push(
      new Paragraph({
        style: "Title",
        children: [
          new TextRun({ text: table.name, font: "Calibri", size: 50, bold: true, color: "1D364F" }),
        ],
        spacing: { after: 280 },
      }),
    );
    const rows = await source.rows(table.id);
    const bodies = rows.map((row) => {
      const body = source.blocks(row.id);
      body.catch(() => undefined); // reported by the ordered await below
      return body;
    });
    for (const [index, row] of rows.entries()) {
      children.push(
        new Paragraph({
          heading: HeadingLevel.HEADING_1,
          children: [
            new TextRun({ text: row.title, font: "Calibri", size: 30, bold: true, color: "1D4F69" }),
          ],
          spacing: { before: 200, after: 100 },
          keepNext: true,
        }),
        ...paragraphsOf(await bodies[index]),
      );
    }
  }
  const document = new Document({
    sections: [
      {
        properties: { page: { margin: { top: 1037, bottom: 1037, left: 1181, right: 1181 } } },
        children,
      },
    ],
    styles: {
      default: {
        document: {
          run: { font: "Calibri", size: 21, color: "28303A" },
          paragraph: { spacing: { after: 80 } },
        },
      },
    },
  });
  return Packer.toBuffer(document);
}
