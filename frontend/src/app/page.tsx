import { Fragment } from "react";
import Link from "next/link";

// Landing page — "Option B" engineered/left-aligned aesthetic (LangChain-style):
// light canvas, navy text, blue accent, mono `//` kickers, bordered sections.
// Server component — no client interactivity needed.

function Logo({ dark = false }: { dark?: boolean }) {
  return (
    <a href="#" className="flex items-center gap-2">
      <span className="inline-block h-5 w-5 rounded-[5px] bg-primary" />
      <span className={`font-display text-lg tracking-tight ${dark ? "text-background" : "text-ink"}`}>
        Laufwise
      </span>
    </a>
  );
}

function Kicker({ children }: { children: React.ReactNode }) {
  return (
    <span className="font-mono text-xs uppercase tracking-widest text-primary">// {children}</span>
  );
}

// Small geometric marks for feature cards (square / circle / diamond).
function Mark({ shape }: { shape: "square" | "circle" | "diamond" }) {
  return (
    <span className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-primary/10">
      <span
        className={`block h-2.5 w-2.5 bg-primary ${
          shape === "circle" ? "rounded-full" : shape === "diamond" ? "rotate-45" : "rounded-[2px]"
        }`}
      />
    </span>
  );
}

function Nav() {
  const items = ["Platform", "Use cases", "Docs", "Pricing"];
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 sm:px-6">
        <div className="flex items-center gap-8">
          <Logo />
          <nav className="hidden items-center gap-6 text-sm text-muted-foreground md:flex">
            {items.map((i) => (
              <a key={i} href={`#${i.split(" ")[0].toLowerCase()}`} className="hover:text-foreground">
                {i}
              </a>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-3">
          <a href="#" className="hidden text-sm text-muted-foreground hover:text-foreground sm:inline">
            Sign in
          </a>
          <a
            href="#"
            className="inline-flex items-center rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            Start building
          </a>
        </div>
      </div>
    </header>
  );
}

function ScreenshotPlaceholder({ label }: { label: string }) {
  return (
    <div className="relative aspect-[4/3] w-full overflow-hidden rounded-xl border border-border bg-surface">
      <div className="absolute inset-0 hatch" />
      <div className="absolute inset-0 flex items-center justify-center">
        <span className="font-mono text-[11px] text-muted-foreground">[ {label} ]</span>
      </div>
    </div>
  );
}

function Hero() {
  const stats = [
    { k: "100%", v: "verified vs state" },
    { k: "0", v: "ungrounded writes" },
    { k: "<10ms", v: "check overhead" },
  ];
  return (
    <section className="relative border-b border-border">
      <div className="pointer-events-none absolute inset-0 wash" />
      <div className="relative mx-auto grid max-w-7xl items-center gap-12 px-5 py-16 sm:px-6 sm:py-20 lg:grid-cols-2 lg:gap-10">
        <div>
          <Kicker>Agentic Platform</Kicker>
          <h1 className="mt-5 font-display text-[clamp(2.2rem,5vw,3.75rem)] leading-[1.05] tracking-tight text-ink">
            The infrastructure for production agents.
          </h1>
          <p className="mt-5 max-w-xl text-base leading-relaxed text-muted-foreground sm:text-lg">
            Run any agent inside an enforced, auditable process contract — conversational
            assistants, copilots, and autonomous workflows — grounded in your real systems of
            record. Prevention, not detection.
          </p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <a
              href="#"
              className="inline-flex items-center justify-center rounded-lg bg-primary px-5 py-3 text-sm font-medium text-primary-foreground hover:opacity-90"
            >
              Start building free
            </a>
            <a
              href="#"
              className="inline-flex items-center justify-center rounded-lg border border-border bg-surface px-5 py-3 text-sm font-medium hover:bg-muted"
            >
              Read the docs →
            </a>
          </div>
          <dl className="mt-10 grid max-w-md grid-cols-3 gap-6">
            {stats.map((s) => (
              <div key={s.v}>
                <dt className="font-display text-2xl text-ink sm:text-3xl">{s.k}</dt>
                <dd className="mt-1 font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
                  {s.v}
                </dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="lg:pl-4">
          <ScreenshotPlaceholder label="operator console — runs & approvals" />
        </div>
      </div>
    </section>
  );
}

function Trusted() {
  const logos = ["NORTHWIND", "VERTEX LABS", "QUANTA", "HELIO", "BRIGHTPATH"];
  return (
    <section className="border-b border-border">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-10 gap-y-3 px-5 py-8 sm:px-6">
        <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
          Trusted by ~
        </span>
        {logos.map((l) => (
          <span key={l} className="font-display text-base tracking-wide text-muted-foreground/70">
            {l}
          </span>
        ))}
      </div>
    </section>
  );
}

function Platform() {
  const features: { t: string; d: string; shape: "square" | "circle" | "diamond" }[] = [
    { t: "Process contracts", d: "Declare each step's preconditions, tool allowlist, approvals, and postconditions. The runbook is the guarantee.", shape: "square" },
    { t: "Grounded in state", d: "Checks evaluate against your real system of record — ERP, CRM, calendar, FHIR — never the model's claim.", shape: "circle" },
    { t: "Tool allowlists", d: "Only declared tools are callable per step. Everything else is refused — enforced twice, defense in depth.", shape: "diamond" },
    { t: "Approval gates", d: "Risky actions block on human sign-off via CLI, webhook, or queue before anything runs.", shape: "square" },
    { t: "Audit & replay", d: "Every run is an append-only episode log plus OTEL spans. Replay any run deterministically.", shape: "circle" },
    { t: "Bring your own agent", d: "Wrap LangGraph, a raw LLM, or MCP behind one adapter. The agent is a plugin; the runtime governs it.", shape: "diamond" },
  ];
  return (
    <section id="platform" className="border-b border-border">
      <div className="mx-auto max-w-7xl px-5 py-20 sm:px-6 sm:py-24">
        <Kicker>Platform</Kicker>
        <h2 className="mt-5 max-w-2xl font-display text-3xl tracking-tight text-ink sm:text-4xl">
          A complete runtime for the agent lifecycle
        </h2>
        <div className="mt-12 grid gap-x-10 gap-y-10 sm:grid-cols-2 lg:grid-cols-3">
          {features.map((f) => (
            <div key={f.t}>
              <Mark shape={f.shape} />
              <h3 className="mt-4 font-medium text-ink">{f.t}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{f.d}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// The actual enforced loop, reused as the product visual.
function RuntimeDiagram() {
  const steps = [
    { n: "01", t: "Precondition", s: "checked vs real state" },
    { n: "02", t: "Tool allowlist", s: "only declared tools" },
    { n: "03", t: "Approval gate", s: "human sign-off", accent: true },
    { n: "04", t: "Execute", s: "the agent acts" },
    { n: "05", t: "Postcondition", s: "verified vs state", accent: true },
  ];
  return (
    <div className="relative overflow-hidden rounded-2xl border border-border bg-surface p-6 shadow-sm sm:p-8">
      <div className="pointer-events-none absolute inset-0 grid-fade" />
      <div className="relative flex flex-col items-stretch gap-3 sm:flex-row sm:items-center">
        {steps.map((step, i) => (
          <Fragment key={step.t}>
            <div
              className={`flex-1 rounded-xl border bg-background px-4 py-4 text-left ${
                step.accent ? "border-primary/40 ring-1 ring-primary/20" : "border-border"
              }`}
            >
              <div className="font-mono text-[11px] text-muted-foreground">{step.n}</div>
              <div className="mt-2 font-medium text-ink">{step.t}</div>
              <div className="mt-1 text-xs text-muted-foreground">{step.s}</div>
            </div>
            {i < steps.length - 1 && (
              <span aria-hidden className="mx-auto rotate-90 text-lg text-muted-foreground sm:rotate-0">
                →
              </span>
            )}
          </Fragment>
        ))}
      </div>
    </div>
  );
}

function EnforcedLoop() {
  return (
    <section className="border-b border-border bg-muted/40">
      <div className="mx-auto max-w-7xl px-5 py-20 sm:px-6">
        <Kicker>The enforced loop</Kicker>
        <h2 className="mt-5 max-w-2xl font-display text-3xl tracking-tight text-ink sm:text-4xl">
          Every action passes through the same contract
        </h2>
        <p className="mt-4 max-w-2xl text-muted-foreground">
          A failed precondition blocks before any tool runs; a failed postcondition rejects the
          outcome — even if the agent claims success.
        </p>
        <div className="mt-10">
          <RuntimeDiagram />
        </div>
      </div>
    </section>
  );
}

function HowItWorks() {
  const steps = [
    { n: "01", k: "define", t: "Define the contract", d: "Write the runbook: ordered steps, pre/postconditions, tool allowlists, approvals." },
    { n: "02", k: "ground", t: "Ground it in state", d: "Bind each check to your real system of record — ERP, CRM, calendar, FHIR, APIs." },
    { n: "03", k: "run", t: "Run & observe", d: "The runtime blocks unsafe actions, rejects ungrounded outcomes, and traces every run." },
  ];
  return (
    <section id="docs" className="border-b border-border">
      <div className="mx-auto max-w-7xl px-5 py-20 sm:px-6">
        <Kicker>How it works</Kicker>
        <h2 className="mt-5 max-w-2xl font-display text-3xl tracking-tight text-ink sm:text-4xl">
          Prototype to production in three steps
        </h2>
        <div className="mt-12 grid gap-8 md:grid-cols-3">
          {steps.map((s) => (
            <div key={s.n} className="border-t border-border pt-5">
              <div className="font-mono text-xs text-muted-foreground">
                {s.n} / {s.k}
              </div>
              <h3 className="mt-3 font-medium text-ink">{s.t}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{s.d}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function PricingCard({
  name,
  price,
  period,
  blurb,
  features,
  cta,
  popular = false,
}: {
  name: string;
  price: string;
  period?: string;
  blurb: string;
  features: string[];
  cta: string;
  popular?: boolean;
}) {
  return (
    <div
      className={`flex flex-col rounded-2xl border p-7 ${
        popular ? "border-transparent bg-ink text-background" : "border-border bg-surface"
      }`}
    >
      <div className="flex items-center justify-between">
        <span className="font-medium">{name}</span>
        {popular && (
          <span className="rounded-full bg-primary px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest text-primary-foreground">
            Popular
          </span>
        )}
      </div>
      <p className={`mt-1 text-sm ${popular ? "opacity-70" : "text-muted-foreground"}`}>{blurb}</p>
      <div className="mt-6 flex items-end gap-1">
        <span className="font-display text-4xl tracking-tight">{price}</span>
        {period && (
          <span className={`pb-1 text-sm ${popular ? "opacity-60" : "text-muted-foreground"}`}>
            {period}
          </span>
        )}
      </div>
      <a
        href="#"
        className={`mt-6 inline-flex items-center justify-center rounded-lg px-4 py-2.5 text-sm font-medium ${
          popular
            ? "bg-primary text-primary-foreground hover:opacity-90"
            : "border border-border bg-background hover:bg-muted"
        }`}
      >
        {cta}
      </a>
      <ul className={`mt-6 space-y-2 text-sm ${popular ? "opacity-80" : "text-muted-foreground"}`}>
        {features.map((f) => (
          <li key={f}>{f}</li>
        ))}
      </ul>
    </div>
  );
}

function Pricing() {
  return (
    <section id="pricing" className="border-b border-border">
      <div className="mx-auto max-w-7xl px-5 py-20 sm:px-6">
        <Kicker>Pricing</Kicker>
        <h2 className="mt-5 max-w-2xl font-display text-3xl tracking-tight text-ink sm:text-4xl">
          Start free. Scale when you do.
        </h2>
        <div className="mt-12 grid gap-6 md:grid-cols-3">
          <PricingCard
            name="Developer"
            price="$0"
            period="/mo"
            blurb="Side projects and prototypes."
            cta="Get started"
            features={["1 project", "Local runtime", "Community support"]}
          />
          <PricingCard
            name="Team"
            price="$—"
            period="/mo"
            blurb="Teams shipping to production."
            cta="Start free trial"
            popular
            features={["Hosted control plane", "Audit & replay", "Priority support"]}
          />
          <PricingCard
            name="Enterprise"
            price="Custom"
            blurb="Scale and compliance."
            cta="Contact sales"
            features={["SSO / RBAC", "Self-hosted option", "Dedicated SLA"]}
          />
        </div>
        <p className="mt-6 font-mono text-[11px] text-muted-foreground">
          {/* placeholder — final tiers & prices TBD */}
          Placeholder pricing — tiers and prices are not final.
        </p>
      </div>
    </section>
  );
}

function CTABand() {
  return (
    <section className="bg-ink text-background">
      <div className="mx-auto flex max-w-7xl flex-col gap-8 px-5 py-16 sm:px-6 sm:py-20 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="font-display text-3xl tracking-tight sm:text-4xl">
            Ship your first governed agent this week.
          </h2>
          <p className="mt-3 max-w-md text-sm opacity-70">
            Free for local development. Pay only when your agents go to production.
          </p>
        </div>
        <div className="flex flex-col gap-3 sm:flex-row">
          <a
            href="#"
            className="inline-flex items-center justify-center rounded-lg bg-primary px-5 py-3 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            Start building free
          </a>
          <a
            href="#"
            className="inline-flex items-center justify-center rounded-lg border border-white/20 px-5 py-3 text-sm font-medium hover:bg-white/10"
          >
            Book a demo
          </a>
        </div>
      </div>
    </section>
  );
}

function Footer() {
  const cols = [
    { t: "Product", l: ["Platform", "Pricing", "Integrations", "Changelog"] },
    { t: "Developers", l: ["Docs", "API reference", "Console", "Status"] },
    { t: "Company", l: ["About", "Security", "Blog", "Careers"] },
  ];
  return (
    <footer className="bg-ink text-background">
      <div className="mx-auto grid max-w-7xl gap-10 px-5 py-14 sm:px-6 md:grid-cols-4">
        <div>
          <Logo dark />
          <p className="mt-4 max-w-xs text-sm opacity-60">
            The infrastructure for production agents.
          </p>
        </div>
        {cols.map((c) => (
          <div key={c.t}>
            <div className="font-mono text-[11px] uppercase tracking-widest opacity-50">{c.t}</div>
            <ul className="mt-4 space-y-2 text-sm">
              {c.l.map((i) =>
                i === "Console" ? (
                  <li key={i}>
                    <Link href="/runs" className="opacity-70 hover:opacity-100">
                      {i}
                    </Link>
                  </li>
                ) : (
                  <li key={i}>
                    <a href="#" className="opacity-70 hover:opacity-100">
                      {i}
                    </a>
                  </li>
                ),
              )}
            </ul>
          </div>
        ))}
      </div>
      <div className="border-t border-white/10">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-5 font-mono text-[11px] opacity-50 sm:px-6">
          <span>© 2026 Laufwise Labs, Inc.</span>
          <span>SOC 2 Type II</span>
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
      <Trusted />
      <Platform />
      <EnforcedLoop />
      <HowItWorks />
      <Pricing />
      <CTABand />
      <Footer />
    </div>
  );
}