import type { Metadata } from "next";
import type { ReactNode } from "react";
import localFont from "next/font/local";
import { ClerkProvider } from "@clerk/nextjs";
import "./globals.css";

// Fonts are vendored, not fetched. `next/font/google` downloads the woff2 from
// fonts.gstatic.com at BUILD time, so a rate-limit against the shared CI IP ranges fails
// `next build` outright — it did, on main, on a commit that had already passed CI twice.
// Same reasoning as pinning the ruff rule set: no outside service gets to redden the build.
//
// These are the `latin` variable files Google itself served for the weights used before
// (Inter 400/500/600/700, JetBrains Mono 400/500) — one file per family covers the range.
// Both are SIL OFL 1.1; see fonts/LICENSE.md.
const sans = localFont({
  src: "./fonts/Inter-latin-variable.woff2",
  weight: "100 900",
  display: "swap",
  variable: "--font-sans",
});
const mono = localFont({
  src: "./fonts/JetBrainsMono-latin-variable.woff2",
  weight: "100 800",
  display: "swap",
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "Laufwise — Infrastructure for the agentic era",
  description:
    "The governed runtime, observability, and audit layer for production AI agents. Prevention, not detection.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <ClerkProvider>
      <html lang="en" className={`${sans.variable} ${mono.variable}`}>
        <body>{children}</body>
      </html>
    </ClerkProvider>
  );
}