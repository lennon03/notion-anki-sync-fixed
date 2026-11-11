import hljs from "highlight.js/lib/common";
import katex from "katex";

import { FIRA_CODE_WOFF2, KATEX_CSS } from "./assets.generated";
import {
  type ExportTable,
  type NotionBlock,
  type NotionSource,
  type RichText,
  type TextSize,
  valueOf,
} from "./types";

const FIRA_CODE_FONT = `data:font/woff2;base64,${FIRA_CODE_WOFF2}`;

// Notion color name -> CSS color (same palette as the Word export).
const COLORS: Record<string, string> = {
  gray: "#787774",
  brown: "#9F6B53",
  orange: "#D9730D",
  yellow: "#CB912F",
  green: "#448361",
  blue: "#337EA9",
  purple: "#9065B0",
  pink: "#C14C8A",
  red: "#D44C47",
  default: "#28303A",
};
function colorOf(value: string | undefined): string {
  return COLORS[String(value ?? "default").replace("_background", "")] ?? COLORS.default;
}
function backgroundOf(value: string | undefined): string | null {
  return String(value ?? "").endsWith("_background") ? colorOf(value) : null;
}

export function escapeHtml(value: unknown): string {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c,
  );
}

function richTextContent(item: RichText): string {
  if (item?.type === "text") return item.text?.content ?? item.plain_text ?? "";
  if (item?.type === "equation") return item.equation?.expression ?? item.plain_text ?? "";
  return item?.plain_text ?? item?.text?.content ?? "";
}

// Mark programming operators so the bundled Fira Code font can render their
// ligatures the same way everywhere.
function renderOperators(value: string): string {
  return escapeHtml(value)
    .replace(/=&gt;/g, '<span class="operator">=&gt;</span>')
    .replace(/-&gt;/g, '<span class="operator">-&gt;</span>');
}

function renderLatex(expression: unknown, displayMode = false): string {
  try {
    return katex.renderToString(String(expression ?? ""), {
      displayMode,
      throwOnError: false,
      output: "htmlAndMathml",
    });
  } catch {
    return escapeHtml(expression);
  }
}

function renderCode(value: string, language: string | undefined): string {
  const normalized = String(language ?? "")
    .toLowerCase()
    .replace(/[\s_]+/g, "-");
  if (!normalized || normalized === "plain-text" || normalized === "text") return escapeHtml(value);
  if (!hljs.getLanguage(normalized)) return escapeHtml(value);
  return hljs.highlight(value, { language: normalized, ignoreIllegals: true }).value;
}

function imageUrl(value: {
  type?: string;
  external?: { url?: string };
  file?: { url?: string };
}): string | null {
  const candidate = value?.type === "external" ? value.external?.url : value?.file?.url;
  if (!candidate) return null;
  try {
    const url = new URL(candidate);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

function plain(items: RichText[] = []): string {
  return items.map((item) => item.plain_text ?? item.text?.content ?? "").join("");
}

function captionHtml(caption: RichText[] = []): string {
  const text = plain(caption);
  return text ? `<figcaption>${escapeHtml(text)}</figcaption>` : "";
}

// Preserve rich-text annotations, matching what the Word export keeps.
function runsHtml(richText: RichText[] = []): string {
  return richText
    .map((item) => {
      if (item.type === "equation") {
        return renderLatex(item.equation?.expression ?? item.plain_text ?? "");
      }
      const text = renderOperators(richTextContent(item));
      if (!text) return "";
      const a = item.annotations ?? {};
      const style: string[] = [];
      if (a.color && a.color !== "default" && !a.color.endsWith("_background")) {
        style.push(`color:${colorOf(a.color)}`);
      }
      const bg = backgroundOf(a.color);
      if (bg) style.push(`background-color:${bg}`);
      let html = text;
      if (a.code) html = `<code>${html}</code>`;
      if (a.bold) html = `<strong>${html}</strong>`;
      if (a.italic) html = `<em>${html}</em>`;
      if (a.underline) html = `<u>${html}</u>`;
      if (a.strikethrough) html = `<s>${html}</s>`;
      return style.length ? `<span style="${style.join(";")}">${html}</span>` : html;
    })
    .join("");
}

function tableHtml(block: NotionBlock, depth: number): string {
  const header = valueOf(block).has_column_header;
  const rows = (block.children ?? []).map((row, index) => {
    const tag = header && index === 0 ? "th" : "td";
    const cells = (valueOf(row).cells ?? []).map((cell) => `<${tag}>${runsHtml(cell)}</${tag}>`);
    return `<tr>${cells.join("")}</tr>`;
  });
  const indent = depth ? ` style="margin-left:${depth * 18}px"` : "";
  return `<table${indent}>${rows.join("")}</table>`;
}

function blockHtml(block: NotionBlock, depth: number): string {
  const value = valueOf(block);
  const runs = () => runsHtml(value.rich_text);
  const margin = depth ? ` style="margin-left:${depth * 18}px"` : "";
  switch (block.type) {
    case "heading_1":
      return `<h1>${runs()}</h1>`;
    case "heading_2":
      return `<h2>${runs()}</h2>`;
    case "heading_3":
      return `<h3>${runs()}</h3>`;
    case "bulleted_list_item":
      return `<ul><li${depth ? ` data-depth="${depth}"` : ""}>${runs()}</li></ul>`;
    case "numbered_list_item":
      return `<ol><li${depth ? ` data-depth="${depth}"` : ""}>${runs()}</li></ol>`;
    case "to_do":
      return `<p class="todo"${margin}>${value.checked ? "☑" : "☐"} ${runs()}</p>`;
    case "quote":
      return `<blockquote${margin}>${runs()}</blockquote>`;
    case "code":
      return `<pre class="code-block"><code class="language-${escapeHtml(value.language ?? "text")}">${renderCode(
        (value.rich_text ?? []).map(richTextContent).join(""),
        value.language,
      )}</code></pre>${captionHtml(value.caption)}`;
    case "callout":
      return `<div class="callout"${margin}>💡 ${runs()}</div>`;
    case "equation":
      return `<div class="equation">${renderLatex(value.expression ?? "", true)}</div>`;
    case "image": {
      const url = imageUrl(value);
      if (!url) {
        return `<p class="image-fallback">${captionHtml(value.caption) || "Image unavailable"}</p>`;
      }
      return `<figure class="notion-image"><img src="${escapeHtml(url)}" alt="${escapeHtml(plain(value.caption))}" />${captionHtml(value.caption)}</figure>`;
    }
    case "divider":
      return `<hr />`;
    case "table":
      return tableHtml(block, depth);
    default:
      if (Array.isArray(value.rich_text) && value.rich_text.length) {
        return `<p${margin}>${runs()}</p>`;
      }
      return "";
  }
}

// Children come nested in the block already; a table renders its own rows.
function blocksHtml(blocks: NotionBlock[], depth = 0): string {
  return blocks
    .flatMap((block) => {
      const own = blockHtml(block, depth);
      if (block.type === "table" || !block.children?.length) return [own];
      return [own, blocksHtml(block.children, depth + 1)];
    })
    .join("\n");
}

const SIZE_PRESETS = {
  small: { scale: 0.82, spacing: 0.72, imageMaxHeight: 140 },
  medium: { scale: 0.82, spacing: 1, imageMaxHeight: 190 },
  large: { scale: 1, spacing: 1, imageMaxHeight: 240 },
};

// The print stylesheet. `size` scales the type. Headings never end a page, but
// no block forbids breaking inside it: content taller than a page must be able
// to split instead of leaving a gap.
function styles(size: TextSize, forPdf: boolean): string {
  const { scale: s, spacing, imageMaxHeight } = SIZE_PRESETS[size] ?? SIZE_PRESETS.large;
  const pt = (base: number) => `${(base * s).toFixed(2)}pt`;
  const step = (base: number) => `${(base * s * spacing).toFixed(2)}pt`;
  // For the PDF the margins come from Chromium, so @page must not add its own.
  const pageRule = forPdf ? "@page { size: A4; }" : "@page { size: A4; margin: 20mm 21mm; }";
  return `
  ${pageRule}
  * { box-sizing: border-box; }
  body { font-family: Calibri, "Segoe UI", system-ui, sans-serif; font-size: ${pt(11)}; line-height: 1.35; color: #28303A; margin: 0; }
  .title { font-size: ${pt(25)}; font-weight: 700; color: #1D364F; margin: 0 0 ${step(14)}; line-height: 1.15; }
  h1 { font-size: ${pt(15)}; font-weight: 700; color: #1D4F69; margin: ${step(10)} 0 ${step(5)}; break-after: avoid; page-break-after: avoid; }
  h2 { font-size: ${pt(13)}; font-weight: 700; color: #1D4F69; margin: ${step(9)} 0 ${step(4)}; break-after: avoid; page-break-after: avoid; }
  h3 { font-size: ${pt(12)}; font-weight: 700; color: #1D4F69; margin: ${step(8)} 0 ${step(4)}; break-after: avoid; page-break-after: avoid; }
  p { margin: 0 0 ${step(4)}; orphans: 3; widows: 3; }
  ul, ol { margin: 0 0 ${step(4)}; padding-left: ${step(22)}; }
  li { margin: 0 0 ${step(2)}; }
  li[data-depth] { list-style: none; }
  blockquote { margin: 0 0 ${step(4)} 8pt; padding: 1pt 0 1pt 10pt; border-left: 3px solid #D3D1CB; font-style: italic; }
  table { border-collapse: collapse; margin: 0 0 ${step(6)}; max-width: 100%; }
  td, th { border: 1px solid #D3D1CB; padding: ${step(3)} ${step(6)}; vertical-align: top; text-align: left; }
  th { background: #F7F7F5; font-weight: 700; }
  pre { margin: 0 0 ${step(6)}; padding: ${step(6)} 8pt; background: #F1F1EF; font-family: Consolas, "Courier New", monospace; font-size: ${pt(10)}; white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere; }
  .code-block code { display: block; padding: 0; background: transparent; border-radius: 0; }
  .hljs-comment, .hljs-quote { color: #6A737D; font-style: italic; }
  .hljs-keyword, .hljs-selector-tag, .hljs-literal { color: #7A3E9D; }
  .hljs-string, .hljs-attr, .hljs-template-variable { color: #2A7B4F; }
  .hljs-number, .hljs-symbol, .hljs-bullet { color: #B05A00; }
  .hljs-title, .hljs-section, .hljs-name { color: #1D4F69; font-weight: 600; }
  .hljs-built_in, .hljs-type, .hljs-variable { color: #9C3A2E; }
  code { font-family: Consolas, "Courier New", monospace; font-size: 0.92em; background: #F1F1EF; padding: 0 2px; border-radius: 2px; }
  @font-face { font-family: "Fira Code Export"; src: url("${FIRA_CODE_FONT}") format("woff2"); font-weight: 400; font-style: normal; }
  .operator { color: inherit; font-family: "Fira Code Export", monospace; font-variant-ligatures: contextual; font-feature-settings: "calt" 1; }
  .callout { margin: 0 0 ${step(6)}; padding: ${step(6)} 8pt; background: #F7F7F5; border-radius: 3px; }
  .todo { margin: 0 0 ${step(3)}; }
  .equation { margin: 0 0 ${step(6)}; overflow-x: auto; }
  .katex-display { margin: 0; overflow-x: auto; overflow-y: hidden; }
  .notion-image { margin: 0 0 ${step(6)}; text-align: center; }
  .notion-image img { display: block; max-width: 100%; max-height: ${imageMaxHeight}mm; height: auto; margin: 0 auto; object-fit: contain; }
  .notion-image figcaption, .image-fallback figcaption { margin-top: ${step(2)}; color: #787774; font-size: ${pt(9)}; text-align: center; }
  .image-fallback { color: #9C3A2E; font-style: italic; }
  hr { border: none; border-top: 1px solid #D3D1CB; margin: ${step(8)} 0; }
  .lesson + .lesson { break-before: page; page-break-before: always; }
  body:has(#export-done) .export-status { display: none; }
  .export-status { position: fixed; top: 12px; right: 12px; z-index: 10; padding: 6px 12px; border-radius: 999px; background: #1D364F; color: #fff; font: 500 13px system-ui, sans-serif; box-shadow: 0 2px 8px rgba(0,0,0,.2); }
  .export-error { margin: 16px 0; padding: 12px 14px; border-radius: 6px; background: #FDEBEC; color: #9C2B2E; font: 14px system-ui, sans-serif; }
  @media print { .export-status { display: none; } }
  .card + .card { margin-top: ${step(14)}; padding-top: ${step(10)}; border-top: 1px solid #ECECEA; }

  /* Screen: the PDF's look on plain white, centered at the width of an A4
     page's text block. Print and the server PDF use print media instead. */
  @media screen {
    html, body { background: #FFFFFF; }
    body { max-width: 210mm; margin: 0 auto; padding: 16px 21mm 20mm; }
    .lesson + .lesson { margin-top: ${step(28)}; padding-top: ${step(20)}; border-top: 1px solid #D3D1CB; }
  }
  @media screen and (max-width: 700px) {
    body { padding: 16px 16px 32px; }
  }
`;
}

function documentHead(tables: ExportTable[], size: TextSize, forPdf: boolean): string {
  return `<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${escapeHtml(tables.map((table) => table.name).join(", "))}</title>
<style>${styles(size, forPdf)}</style>
</head>
<body>
`;
}

/**
 * The body piece by piece, in order, as soon as each is ready. Every table and
 * row is requested at once (the source limits how many run together), so the
 * pieces can be streamed while later ones are still loading.
 */
async function* bodyParts(
  tables: ExportTable[],
  source: NotionSource,
): AsyncGenerator<string> {
  let katexSent = false;

  const jobs = tables.map((table) => {
    const job = source.rows(table.id).then((rows) =>
      rows.map((row) => {
        const card = source.blocks(row.id).then(
          (blocks) => `<section class="card">
<h1>${escapeHtml(row.title)}</h1>
${blocksHtml(blocks)}</section>`,
        );
        card.catch(() => undefined); // the ordered await below reports it
        return card;
      }),
    );
    job.catch(() => undefined);
    return job;
  });

  for (let i = 0; i < tables.length; i++) {
    const cards = await jobs[i];
    yield `<article class="lesson">
<p class="title">${escapeHtml(tables[i].name)}</p>
`;
    for (const card of cards) {
      let html = await card;
      // The math stylesheet is large: send it once, and only if a card has math.
      if (!katexSent && html.includes('class="katex')) {
        katexSent = true;
        html = `<style>${KATEX_CSS}</style>\n${html}`;
      }
      yield `${html}\n`;
    }
    yield "</article>\n";
  }
}

/** The whole document, for the PDF renderer. */
export async function exportHtml(
  tables: ExportTable[],
  source: NotionSource,
  { size = "large", forPdf = false }: { size?: TextSize; forPdf?: boolean } = {},
): Promise<string> {
  const parts = [documentHead(tables, size, forPdf)];
  for await (const part of bodyParts(tables, source)) parts.push(part);
  parts.push("</body>\n</html>");
  return parts.join("");
}

/**
 * The same document streamed to a browser: the head and a loading chip go out
 * at once, then each card as it arrives. The page has no script at all (its
 * Content-Security-Policy forbids them): a marker at the end hides the chip.
 */
export async function* streamHtml(
  tables: ExportTable[],
  source: NotionSource,
  { size = "large", describe }: { size?: TextSize; describe: (error: unknown) => string },
): AsyncGenerator<string> {
  yield documentHead(tables, size, false);
  yield `<div class="export-status" role="status">Loading from Notion…</div>
`;
  try {
    yield* bodyParts(tables, source);
  } catch (error) {
    yield `<div class="export-error"><strong>The export failed.</strong> ${escapeHtml(describe(error))}</div>
`;
  }
  yield `<div id="export-done"></div>
</body>
</html>`;
}
