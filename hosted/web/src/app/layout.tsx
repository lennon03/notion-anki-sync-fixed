import "@fontsource-variable/inter";
import "./globals.css";

import type { Metadata } from "next";
import { cookies } from "next/headers";
import type { ReactNode } from "react";

import { THEME_COOKIE, themeId } from "~/lib/theme";

export const metadata: Metadata = {
  title: "Notion Sync",
  description: "Keeps an AnkiWeb collection in sync with Notion.",
  robots: { index: false, follow: false },
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const theme = themeId((await cookies()).get(THEME_COOKIE)?.value);
  return (
    <html lang="en" data-theme-id={theme}>
      <body>{children}</body>
    </html>
  );
}
