import { Fragment } from "react";
import Link from "next/link";

// Landing page — light, minimal aesthetic (LangChain-style). Rebuilt on Next.js +
// Tailwind. No client interactivity needed, so this stays a server component.

function Logo() {
  return (
    <div className="flex items-center gap-2">
      <svg width="22" height="22" viewBox="0 0 22 22" className="text-ink">
        <path
          d="M3 11 C 7 3, 15 3, 19 11 C 15 19, 7 19, 3 11 Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.2"
        />
        <circle cx="11" cy="11" r="2.4" fill="currentColor" />
      </svg>
      <span className="font-display text-xl tracking-tight">Laufwise</span>
    </div>
  );
}

function Nav() {
  const items = ["Platform", "Runtime", "Workloads", "Docs", "Pricing"];
  return (
    <header className="sticky top-0 z-30 backdrop-blur-md bg-background/70 border-b border-border">
      <div className="mx-auto max-w-7xl px-6 h-16 flex items-center justify-between">
        <Logo />
        <nav className="hidden md:flex items-center gap-8 text-sm text-muted-foreground">
          {items.map((i) => (
            <a key={i} href={`#${i.toLowerCase()}`} className="hover:text-foreground transition-colors">
              {i}
            </a>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <a href="#" className="hidden sm:inline-flex chip">
            Sign in
          </a>
          <a
            href="#"
            className="inline-flex items-center rounded-full bg-foreground text-background px-4 py-2 text-sm font-medium hover:opacity-90 transition"
          >
            Get a demo
          </a>
        </div>
      </div>
    </header>
  );
}

// The hero visual: a clean geometric diagram of the actual enforced loop, not abstract
// art. Horizontal on desktop, stacked on mobile.
function RuntimeDiagram() {
  const steps = [
    { n: "01", t: "Precondition", s: "checked vs real state" },
    { n: "02", t: "Tool allowlist", s: "only declared tools" },
    { n: "03", t: "Approval gate", s: "human sign-off", accent: true },
    { n: "04", t: "Execute", s: "the agent acts" },
    { n: "05", t: "Postcondition", s: "verified vs state", accent: true },
  ];
  return (
    <div className="relative overflow-hidden rounded-3xl border border-border bg-surface p-6 sm:p-10 shadow-sm">
      <div className="pointer-events-none absolute inset-0 grid-fade" />
      <div className="relative">
        <div className="mb-6 flex items-center justify-between">
          <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
            The enforced loop
          </span>
          <span className="font-mono text-[11px] text-muted-foreground">runtime.laufwise.dev</span>
        </div>
        <div className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-center">
          {steps.map((step, i) => (
            <Fragment key={step.t}>
              <div
                className={`flex-1 rounded-2xl border bg-background px-4 py-4 text-left ${
                  step.accent ? "border-primary/40 ring-1 ring-primary/20" : "border-border"
                }`}
              >
                <div className="font-mono text-[11px] text-muted-foreground">{step.n}</div>
                <div className="mt-2 font-medium text-ink">{step.t}</div>
                <div className="mt-1 text-xs text-muted-foreground">{step.s}</div>
              </div>
              {i < steps.length - 1 && (
                <span
                  aria-hidden
                  className="mx-auto text-lg text-muted-foreground rotate-90 sm:rotate-0"
                >
                  →
                </span>
              )}
            </Fragment>
          ))}
        </div>
        <p className="mt-6 text-center text-xs sm:text-sm text-muted-foreground">
          Every step runs in order. A failed precondition{" "}
          <span className="font-medium text-ink">blocks before any tool runs</span>; a failed
          postcondition <span className="font-medium text-ink">rejects the outcome</span> — even
          if the agent claims success.
        </p>
      </div>
    </div>
  );
}

function Hero() {
  return (
    <section className="relative overflow-hidden">
      <div className="pointer-events-none absolute inset-0 wash" />
      <div className="relative mx-auto max-w-5xl px-6 pt-16 sm:pt-24 pb-10 text-center">
        <div className="flex flex-wrap items-center justify-center gap-2">
          <span className="chip">Agent Runtime</span>
          <span className="chip">Grounded &amp; Auditable</span>
        </div>
        <h1 className="mt-8 font-display text-[clamp(2.2rem,6.5vw,5rem)] leading-[1.05] tracking-tight text-ink">
          Agent governance is a
          <br className="hidden sm:block" /> new discipline for the{" "}
          <span className="italic font-normal text-primary">agentic</span> era.
        </h1>
        <p className="mt-6 mx-auto max-w-2xl text-base sm:text-lg text-muted-foreground leading-relaxed">
          Laufwise runs any agent inside an enforced, auditable process contract — grounded in
          your real systems of record. The action is permitted before it runs, and the outcome
          is verified after. Prevention, not detection.
        </p>
        <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3">
          <a
            href="#"
            className="w-full sm:w-auto inline-flex justify-center items-center rounded-full bg-foreground text-background px-6 py-3 text-sm font-medium hover:opacity-90"
          >
            Start building →
          </a>
          <a
            href="#"
            className="w-full sm:w-auto inline-flex justify-center items-center rounded-full border border-border bg-surface px-6 py-3 text-sm font-medium hover:bg-muted"
          >
            Read the docs
          </a>
        </div>
      </div>

      <div className="relative mx-auto max-w-6xl px-6 pb-16 sm:pb-24">
        <RuntimeDiagram />
      </div>
    </section>
  );
}

function Logos() {
  const logos = ["NORTHWIND", "ACME LABS", "OBSCURA", "PARALLAX", "FOUNDRY", "AXIOM"];
  return (
    <section className="border-y border-border bg-surface/60">
      <div className="mx-auto max-w-7xl px-6 py-10 flex flex-wrap items-center justify-between gap-x-10 gap-y-4">
        <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
          Trusted by teams shipping agents at scale
        </span>
        <div className="flex flex-wrap items-center gap-x-10 gap-y-3">
          {logos.map((l) => (
            <span key={l} className="font-display text-lg text-muted-foreground/80 tracking-wide">
              {l}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}

function SectionHeader({ kicker, title, sub }: { kicker: string; title: string; sub?: string }) {
  return (
    <div className="text-center mb-14">
      <div className="flex justify-center">
        <span className="chip">{kicker}</span>
      </div>
      <h2 className="mt-6 font-display text-4xl md:text-5xl tracking-tight text-ink">{title}</h2>
      {sub && <p className="mt-4 mx-auto max-w-xl text-muted-foreground">{sub}</p>}
    </div>
  );
}

function Features() {
  const items = [
    {
      t: "Process Contracts",
      d: "Declare each step's preconditions, tool allowlist, approval policy, and postconditions. The runbook is the guarantee — not the prompt.",
      code: "step({ pre, tools, approval, post })",
    },
    {
      t: "Grounded in State",
      d: "Checks evaluate against your real system of record — ERP, CRM, calendar, FHIR — never against the model's claim.",
      code: "verify(vendor.exists == true)",
    },
    {
      t: "Tool Allowlists",
      d: "Only the declared tools are callable for a step. Everything else is refused — enforced twice, defense in depth.",
      code: "tools: [create_vendor_draft]",
    },
    {
      t: "Approval Gates",
      d: "Risky actions block on human sign-off via CLI, webhook, or queue before anything executes.",
      code: "approve.when(risk >= 'medium')",
    },
    {
      t: "Audit & Replay",
      d: "Every run is an append-only episode log plus OTEL spans. Replay any run deterministically.",
      code: "rh replay run_8a2f...",
    },
    {
      t: "Bring Your Own Agent",
      d: "Wrap LangGraph, a raw LLM, or MCP behind one adapter. The agent is a plugin; the runtime governs it.",
      code: "adapter: langgraph | mcp",
    },
  ];
  return (
    <section id="platform" className="mx-auto max-w-7xl px-6 py-28">
      <SectionHeader
        kicker="Platform"
        title="One runtime for any agent."
        sub="From the first prompt to the millionth production run — built for engineers who treat agents as software."
      />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-px bg-border rounded-3xl overflow-hidden border border-border">
        {items.map((it) => (
          <div key={it.t} className="bg-surface p-7 group hover:bg-muted/50 transition-colors">
            <div className="flex items-center justify-between">
              <h3 className="font-display text-2xl tracking-tight">{it.t}</h3>
              <span className="text-muted-foreground group-hover:text-foreground transition">↗</span>
            </div>
            <p className="mt-3 text-sm text-muted-foreground leading-relaxed">{it.d}</p>
            <div className="mt-6 font-mono text-[11px] text-muted-foreground border-t border-dashed border-border pt-3">
              {it.code}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function HowItWorks() {
  const steps = [
    { n: "01", t: "Define", d: "Write the process contract: ordered steps, pre/postconditions, tool allowlists, approvals." },
    { n: "02", t: "Ground", d: "Bind each check to your real system of record — ERP, CRM, calendar, FHIR, APIs." },
    { n: "03", t: "Enforce", d: "The runtime blocks unsafe actions before they run and rejects ungrounded outcomes." },
    { n: "04", t: "Improve", d: "Mine episode logs for where runs block and postconditions reject. A closed loop." },
  ];
  return (
    <section id="runtime" className="border-t border-border bg-surface/40">
      <div className="mx-auto max-w-7xl px-6 py-28">
        <SectionHeader kicker="Workflow" title="Engineer agents, don't babysit them." />
        <div className="grid md:grid-cols-4 gap-px bg-border border border-border rounded-3xl overflow-hidden">
          {steps.map((s) => (
            <div key={s.n} className="bg-background p-8">
              <div className="font-mono text-xs text-muted-foreground">{s.n}</div>
              <div className="mt-6 font-display text-2xl">{s.t}</div>
              <p className="mt-3 text-sm text-muted-foreground leading-relaxed">{s.d}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Workloads() {
  const items = [
    { t: "Conversational", d: "Voice & chat agents that book, triage, and escalate — governed mid-conversation." },
    { t: "Back-office Task", d: "Write-action workflows where premature or unsafe writes are blocked behind approval." },
    { t: "Document & Workflow", d: "Read/verify agents whose every claim is grounded in a real source document." },
  ];
  return (
    <section id="workloads" className="mx-auto max-w-7xl px-6 py-28">
      <SectionHeader
        kicker="Workloads"
        title="Many agents. One control plane."
        sub="Swap the runbook and the surface — the runtime is unchanged. That is the test of a primitive."
      />
      <div className="grid md:grid-cols-3 gap-px bg-border border border-border rounded-3xl overflow-hidden">
        {items.map((it) => (
          <div key={it.t} className="bg-surface p-8 hover:bg-muted/50 transition-colors">
            <h3 className="font-display text-2xl tracking-tight">{it.t}</h3>
            <p className="mt-3 text-sm text-muted-foreground leading-relaxed">{it.d}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function Stats() {
  const stats = [
    { k: "100%", v: "actions verified against real state" },
    { k: "0", v: "ungrounded writes shipped" },
    { k: "<10ms", v: "contract check overhead" },
    { k: "1 spec", v: "many workloads" },
  ];
  return (
    <section className="mx-auto max-w-7xl px-6 py-28">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-8 md:gap-4">
        {stats.map((s) => (
          <div key={s.v} className="text-center md:border-r last:border-r-0 border-border">
            <div className="font-display text-5xl md:text-6xl text-ink tracking-tight">{s.k}</div>
            <div className="mt-3 font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
              {s.v}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function Quote() {
  return (
    <section className="mx-auto max-w-4xl px-6 py-24 text-center">
      <p className="font-display italic text-3xl md:text-4xl leading-snug text-ink">
        “Laufwise turned our agent from a demo we hoped would behave into a system we can
        prove behaves. It blocks the bad action before it happens.”
      </p>
      <div className="mt-8 font-mono text-xs text-muted-foreground">
        MAYA OKONKWO — HEAD OF AI, NORTHWIND
      </div>
    </section>
  );
}

function CTA() {
  return (
    <section className="mx-auto max-w-7xl px-6 pb-28">
      <div className="relative overflow-hidden rounded-3xl bg-ink text-background p-10 sm:p-12 md:p-20 text-center">
        <div className="relative">
          <h2 className="font-display text-4xl md:text-6xl tracking-tight">
            Govern your first agent <span className="italic opacity-80">this week.</span>
          </h2>
          <p className="mt-5 text-sm md:text-base opacity-70 max-w-lg mx-auto">
            Free for local development. Pay only when your agents go to production.
          </p>
          <div className="mt-8 flex items-center justify-center gap-3">
            <a href="#" className="inline-flex rounded-full bg-background text-ink px-5 py-3 text-sm font-medium">
              Start building →
            </a>
            <a
              href="#"
              className="inline-flex rounded-full border border-white/20 px-5 py-3 text-sm font-medium hover:bg-white/10"
            >
              Talk to engineering
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}

function Footer() {
  const cols = [
    { t: "Platform", l: ["Runtime", "Contracts", "Providers", "Audit & Replay"] },
    { t: "Workloads", l: ["Conversational", "Back-office", "Document"] },
    { t: "Company", l: ["About", "Security", "Docs", "Contact"] },
  ];
  return (
    <footer className="border-t border-border bg-surface/60">
      <div className="mx-auto max-w-7xl px-6 py-16 grid md:grid-cols-4 gap-10">
        <div>
          <Logo />
          <p className="mt-4 text-sm text-muted-foreground max-w-xs">
            Infrastructure for the agentic era.
          </p>
        </div>
        {cols.map((c) => (
          <div key={c.t}>
            <div className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
              {c.t}
            </div>
            <ul className="mt-4 space-y-2 text-sm">
              {c.l.map((i) => (
                <li key={i}>
                  <a className="hover:text-foreground text-muted-foreground" href="#">
                    {i}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="border-t border-border">
        <div className="mx-auto max-w-7xl px-6 py-5 flex items-center justify-between font-mono text-[11px] text-muted-foreground">
          <span>© {new Date().getFullYear()} Laufwise Labs, Inc.</span>
          <Link href="/runs" className="hover:text-foreground">
            Console →
          </Link>
        </div>
      </div>
    </footer>
  );
}

export default function Landing() {
  return (
    <div className="min-h-screen">
      <Nav />
      <Hero />
      <Logos />
      <Features />
      <HowItWorks />
      <Workloads />
      <Stats />
      <Quote />
      <CTA />
      <Footer />
    </div>
  );
}