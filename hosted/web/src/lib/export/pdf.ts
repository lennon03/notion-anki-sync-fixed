import chromium from "@sparticuz/chromium";
import puppeteer from "puppeteer-core";

// A "Page X/Y" footer at the bottom right. Chrome fills in the page numbers
// through the classes it reserves for displayHeaderFooter.
const FOOTER = `
  <div style="font-size:9px;width:100%;padding:0 15mm 8px 15mm;display:flex;justify-content:flex-end;color:#8A8F98;font-family:Calibri,'Segoe UI',system-ui,sans-serif;">
    <span>Page <span class="pageNumber"></span>/<span class="totalPages"></span></span>
  </div>`;

// @sparticuz/chromium ships a Linux-only binary (for Vercel). Elsewhere, such
// as local development on Windows or macOS, use an installed Chrome or CHROME_PATH.
const LOCAL_CHROME: Record<string, string> = {
  win32: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  darwin: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
};

async function launchOptions() {
  const local = process.env.CHROME_PATH || LOCAL_CHROME[process.platform];
  if (local) return { executablePath: local, headless: true };
  return {
    args: chromium.args,
    executablePath: await chromium.executablePath(),
    headless: true,
  };
}

/** Renders the document to an A4 PDF with headless Chromium. */
export async function renderPdf(html: string, marginMm = 20): Promise<Buffer> {
  const browser = await puppeteer.launch(await launchOptions());
  try {
    const page = await browser.newPage();
    // The document is made of text we escaped; nothing in it needs a script.
    await page.setJavaScriptEnabled(false);
    // "load" waits for the images too. The HTML carries its own print layout.
    await page.setContent(html, { waitUntil: "load" });
    const pdf = await page.pdf({
      format: "A4",
      printBackground: true,
      displayHeaderFooter: true,
      headerTemplate: "<span></span>",
      footerTemplate: FOOTER,
      margin: {
        top: `${marginMm}mm`,
        bottom: `${marginMm}mm`,
        left: `${marginMm}mm`,
        right: `${marginMm}mm`,
      },
    });
    return Buffer.from(pdf);
  } finally {
    await browser.close();
  }
}
