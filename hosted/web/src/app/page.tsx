import { Dashboard } from "~/components/Dashboard";
import { Landing } from "~/components/landing/Landing";
import { sessionToken } from "~/lib/worker";

export default async function Home() {
  if (!(await sessionToken())) return <Landing />;
  return <Dashboard />;
}
