"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  OrganizationSwitcher,
  UserButton,
  useOrganization,
} from "@clerk/nextjs";
import { useState, type ReactNode } from "react";

// The four things a practice owner does here: build agents, read what they did, see how it is
// going, and check what the platform enforced. Everything else lives inside one of them.
const navigation = [
  { href: "/studio", title: "Agents", symbol: "◈" },
  { href: "/studio/history", title: "Run history", symbol: "↗" },
  { href: "/studio/dashboard", title: "Dashboard", symbol: "▤" },
  { href: "/studio/governance", title: "Governance", symbol: "⛨" },
];
export function WorkspaceShell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { organization } = useOrganization();
  const [open, setOpen] = useState(false);
  return (
    <div className="min-h-screen bg-muted/40 text-foreground lg:flex">
      <aside className="border-b border-border bg-surface lg:fixed lg:inset-y-0 lg:w-56 lg:border-b-0 lg:border-r">
        <div className="flex h-20 items-center justify-between px-6">
          <Link
            href="/studio"
            className="flex items-center gap-2.5 text-xl font-semibold tracking-tight"
          >
            <span className="grid h-8 w-8 place-items-center rounded-xl bg-primary text-base text-white">
              L
            </span>
            Laufwise
          </Link>
          <button
            className="studio-secondary lg:hidden"
            aria-expanded={open}
            aria-label="Toggle navigation"
            onClick={() => setOpen(!open)}
          >
            ☰
          </button>
        </div>
        <div className={`${open ? "block" : "hidden"} px-3 pb-5 lg:block`}>
          <p className="px-3 pb-3 pt-5 text-xs font-medium uppercase tracking-widest text-muted-foreground">
            Workspace
          </p>
          <nav aria-label="Workspace" className="space-y-1">
            {navigation.map((item) => {
              const active =
                item.href === "/studio"
                  ? path === "/studio" ||
                    path.startsWith("/studio/agents/") ||
                    path.startsWith("/studio/configure/")
                  : path.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setOpen(false)}
                  aria-current={active ? "page" : undefined}
                  className={`flex items-center gap-3 rounded-lg px-3 py-3 text-sm transition ${active ? "bg-white font-medium text-ink shadow-sm ring-1 ring-border" : "text-muted-foreground hover:bg-muted hover:text-ink"}`}
                >
                  <span aria-hidden className="text-lg">
                    {item.symbol}
                  </span>
                  {item.title}
                </Link>
              );
            })}
          </nav>
        </div>
        <div className="hidden border-t border-border p-5 lg:absolute lg:inset-x-0 lg:bottom-0 lg:block">
          <p className="text-xs font-medium">Built for your practice</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            Every booking checked.
            <br />
            Every conversation traceable.
          </p>
        </div>
      </aside>
      <div className="min-w-0 flex-1 lg:ml-56">
        <header className="flex h-16 items-center justify-between gap-3 border-b border-border bg-white px-5 sm:px-8">
          <div className="min-w-0 truncate text-sm text-muted-foreground">
            {organization?.name ?? "Your workspace"}
            <span className="mx-3 text-border">/</span>
            <span className="text-ink">Studio</span>
          </div>
          <div className="flex items-center gap-3">
            <OrganizationSwitcher
              hidePersonal
              afterSelectOrganizationUrl="/studio"
            />
            <UserButton />
          </div>
        </header>
        <div key={organization?.id ?? "no-organization"}>{children}</div>
      </div>
    </div>
  );
}
