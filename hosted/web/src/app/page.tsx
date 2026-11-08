import { redirect } from "next/navigation";

import { Dashboard } from "~/components/Dashboard";
import { sessionToken } from "~/lib/worker";

export default async function Home() {
  if (!(await sessionToken())) redirect("/login");
  return <Dashboard />;
}
