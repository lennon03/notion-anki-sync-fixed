import { Landing } from "~/components/landing/Landing";
import { sessionToken } from "~/lib/worker";

/** The landing page for everyone, including an owner who is signed in. */
export default async function About() {
  return <Landing signedIn={Boolean(await sessionToken())} />;
}
