import type { Config } from "tailwindcss";

// Color tokens are CSS variables holding oklch channels (L C H) so Tailwind can inject
// alpha via <alpha-value> — e.g. bg-surface/60 -> oklch(... / 0.6).
export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "oklch(var(--background) / <alpha-value>)",
        foreground: "oklch(var(--foreground) / <alpha-value>)",
        ink: "oklch(var(--ink) / <alpha-value>)",
        surface: "oklch(var(--surface) / <alpha-value>)",
        muted: "oklch(var(--muted) / <alpha-value>)",
        "muted-foreground": "oklch(var(--muted-foreground) / <alpha-value>)",
        border: "oklch(var(--border) / <alpha-value>)",
        input: "oklch(var(--input) / <alpha-value>)",
        primary: "oklch(var(--primary) / <alpha-value>)",
        "primary-foreground": "oklch(var(--primary-foreground) / <alpha-value>)",
        accent: "oklch(var(--accent) / <alpha-value>)",
      },
      fontFamily: {
        // Display is sans (LangChain aesthetic); weight/tracking set in globals.css.
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        display: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;