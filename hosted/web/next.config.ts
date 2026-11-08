import type { NextConfig } from "next";

const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  // Next's bootstrap scripts are inline
  `script-src 'self' 'unsafe-inline'${process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self'",
  "img-src 'self' data:",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "form-action 'self'",
  "base-uri 'self'",
].join("; ");

// The live HTML export is a document of Notion content, not an app page: no
// scripts at all, and it may load Notion's images. Its fonts are data URIs.
const EXPORT_VIEW_CSP = [
  "default-src 'none'",
  "style-src 'unsafe-inline'",
  "img-src https: data:",
  "font-src data:",
  "frame-ancestors 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join("; ");

// The launcher you embed in Notion. It shows one link to the exporter and never
// reads a session or any Notion content, so Notion alone may frame it. Every
// other page stays unframeable.
const EMBED_CSP = CONTENT_SECURITY_POLICY.replace(
  "frame-ancestors 'none'",
  "frame-ancestors https://www.notion.so https://notion.so https://*.notion.site",
);

const config: NextConfig = {
  poweredByHeader: false,
  // Headless Chromium is a binary, and these read their own files: leave them
  // out of the bundle. The Chromium archive must still be shipped with the PDF route.
  serverExternalPackages: ["@sparticuz/chromium", "puppeteer-core"],
  outputFileTracingIncludes: {
    "/api/export": ["../../node_modules/@sparticuz/chromium/bin/**"],
  },
  async headers() {
    // When two rules set the same header the later one wins.
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: CONTENT_SECURITY_POLICY },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
      {
        // X-Frame-Options can't allow one origin, so the embed page goes without it
        source: "/:path((?!embed$).*)",
        headers: [{ key: "X-Frame-Options", value: "DENY" }],
      },
      {
        source: "/export/view",
        headers: [{ key: "Content-Security-Policy", value: EXPORT_VIEW_CSP }],
      },
      {
        source: "/embed",
        headers: [{ key: "Content-Security-Policy", value: EMBED_CSP }],
      },
    ];
  },
};

export default config;
