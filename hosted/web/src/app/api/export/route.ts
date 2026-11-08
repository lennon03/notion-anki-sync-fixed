import { NextResponse } from "next/server";

import { exportDocx } from "~/lib/export/docx";
import { exportHtml } from "~/lib/export/html";
import { renderPdf } from "~/lib/export/pdf";
import { fileName, parseSize, parseTables } from "~/lib/export/request";
import { failure, workerSource } from "~/lib/export/source";
import { clientIp, forbidden, sameOrigin, sessionToken } from "~/lib/worker";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
// Headless Chromium starts slowly when the function is cold.
export const maxDuration = 60;

function problem(status: number, error: string, message: string) {
  return NextResponse.json({ error, message }, { status });
}

/** Builds a PDF or Word file from tables the signed-in owner chose. */
export async function POST(request: Request) {
  if (!sameOrigin(request)) return forbidden();
  const session = await sessionToken();
  if (!session) return problem(401, "no_session", "Sign in first.");

  const body = await request.json().catch(() => null);
  const tables = parseTables(body?.tables);
  const format = body?.format;
  if (!tables || (format !== "pdf" && format !== "docx")) {
    return problem(400, "invalid_request", "Choose at least one table and a format.");
  }

  const source = workerSource(session, await clientIp());
  const name = tables.length === 1 ? tables[0].name : "tables";
  try {
    if (format === "pdf") {
      const html = await exportHtml(tables, source, { size: parseSize(body?.size), forPdf: true });
      return file(await renderPdf(html), "application/pdf", fileName(name, "pdf"));
    }
    return file(
      await exportDocx(tables, source),
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      fileName(name, "docx"),
    );
  } catch (error) {
    const { status, code, message } = failure(error);
    return problem(status, code, message);
  }
}

function file(content: Buffer, type: string, name: string) {
  return new Response(new Uint8Array(content), {
    headers: {
      "Content-Type": type,
      "Content-Disposition": `attachment; filename="${name}"`,
      "Cache-Control": "private, no-store",
    },
  });
}
