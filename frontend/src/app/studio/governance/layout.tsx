"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { StudioTrail } from "@/components/studio/WorkspaceShell";

// Governance is what the platform enforces on your behalf: the contracts agents run inside, and
// the real accounts a write is checked against. Both are read the same way, so they share a header.

const TABS = [
  ["/studio/governance", "Contracts"],
  ["/studio/governance/connections", "Connections"],
];

export default function GovernanceLayout({
  children,
}: {
  children: ReactNode;
}) {
  const path = usePathname();
  return (
    <div>
      <StudioTrail crumbs={[{ label: "Governance" }]} />
      <div className="border-b border-border bg-white px-5 pt-8 sm:px-8">
        <div className="mx-auto max-w-5xl">
          <p className="studio-eyebrow text-primary">What the platform enforces</p>
          <h1 className="mt-2.5 text-[28px] font-semibold tracking-tight text-ink">
            Governance
          </h1>
          <nav aria-label="Governance" className="mt-5 flex gap-1">
            {TABS.map(([href, label]) => {
              const active = path === href;
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={`-mb-px border-b-2 px-4 py-2.5 text-sm transition ${active ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-ink"}`}
                >
                  {label}
                </Link>
              );
            })}
          </nav>
        </div>
      </div>
      {children}
    </div>
  );
}
