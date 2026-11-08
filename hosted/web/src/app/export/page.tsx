import { redirect } from "next/navigation";

import { TableExporter } from "~/components/TableExporter";
import { isNotionId } from "~/lib/export/request";
import { sessionToken } from "~/lib/worker";

export const metadata = { title: "Table Exporter" };

export default async function ExportPage({
  searchParams,
}: {
  searchParams: Promise<{ id?: string | string[] }>;
}) {
  if (!(await sessionToken())) redirect("/login");
  const { id } = await searchParams;
  return <TableExporter pageId={isNotionId(id) ? id.replace(/-/g, "").toLowerCase() : null} />;
}
