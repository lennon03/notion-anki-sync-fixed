import { Button } from "~/components/ui/button";
import { isNotionId, plainId } from "~/lib/export/request";

export const metadata = { title: "Export a Notion page" };

/**
 * What you embed in a Notion page. Notion frames it in a sandbox that blocks
 * downloads, so it only links out to the exporter in a new tab, where the
 * owner's session applies. It reads no cookie, calls no API and shows nothing
 * from Notion, which is why next.config.ts lets Notion (and only Notion) frame it.
 */
export default async function Embed({
  searchParams,
}: {
  searchParams: Promise<{ id?: string | string[] }>;
}) {
  const { id } = await searchParams;
  const href = isNotionId(id) ? `/export?id=${plainId(id)}` : "/export";
  return (
    <div className="flex min-h-svh items-center justify-center bg-background px-4 py-4 text-foreground">
      <section className="dialog-glass flex w-full max-w-xl items-center justify-between gap-4 rounded-2xl border p-4">
        <div className="min-w-0">
          <h1 className="text-sm font-semibold">Export this page's tables</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            {isNotionId(id)
              ? "Opens the Table Exporter in a new tab. You sign in there."
              : "No page was set in the embed link. The exporter lets you choose one."}
          </p>
        </div>
        <Button size="sm" render={<a href={href} target="_blank" rel="noopener noreferrer" />}>
          Open exporter
        </Button>
      </section>
    </div>
  );
}
