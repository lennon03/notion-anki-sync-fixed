import type { Metadata } from "next";

import { Docs } from "~/components/docs/Docs";
import { sessionToken } from "~/lib/worker";

export const metadata: Metadata = {
  title: "Guide · Notion Sync",
  description: "How to write Anki cards in a Notion page.",
};

/** The user guide, public like the landing page. */
export default async function DocsPage() {
  return <Docs signedIn={Boolean(await sessionToken())} />;
}
