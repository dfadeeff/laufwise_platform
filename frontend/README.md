# Frontend — operator console (Next.js)

The console for the governed agent runtime: inspect runbooks (process contracts), watch
runs and their per-step results (OK / BLOCKED / REJECTED), and resolve pending approvals.

## Layout

```
src/
├── app/              # Next.js App Router (routes = pages)
│   ├── layout.tsx    # shell + fonts (next/font) + globals.css
│   ├── globals.css   # Tailwind + design tokens (.chip, .mesh-bg, oklch theme)
│   ├── page.tsx      # marketing landing
│   └── runs/         # operator console — runs view
├── lib/              # backend API client (only place that calls the backend)
└── types/            # shared TS types (mirror backend DTOs)
```

Separation of concerns: components render, `lib/api.ts` is the only place that talks to
the backend, `types/` mirrors the backend wire models.

**Design system:** Tailwind CSS with oklch color tokens defined as CSS variables in
`globals.css` (so `bg-surface/60`-style alpha works), fonts via `next/font`
(Instrument Serif for display, Inter for body, JetBrains Mono for code), and two custom
utilities: `.chip` (pill label) and `.mesh-bg` (gradient mesh).

## Run

```bash
npm install
cp .env.example .env.local
npm run dev        # http://localhost:3000
```

Needs the backend running at `NEXT_PUBLIC_API_BASE_URL` (default http://localhost:8000/api/v1).