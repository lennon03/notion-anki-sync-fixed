import { redirect } from "next/navigation";

import { LoginForm } from "~/components/LoginForm";
import { sessionToken } from "~/lib/worker";

export default async function Login() {
  if (await sessionToken()) redirect("/");
  return <LoginForm />;
}
