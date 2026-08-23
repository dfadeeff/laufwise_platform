import Link from "next/link";
import { OrganizationSwitcher, UserButton } from "@clerk/nextjs";

// Mirrors the /runs ConsoleHeader; Studio pages get real tab links (Studio | Runs).
// The right cluster carries the Clerk org switcher (org = tenant, ADR-0003) + user menu.
export function StudioHeader({ active }: { active: "studio" | "voice" | "runs" }) {
  const tabs = [
    { key: "studio", label: "Studio", href: "/studio" },
    { key: "voice", label: "Voice test", href: "/studio/voice" },
    { key: "runs", label: "Runs", href: "/runs" },
  ] as const;
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 sm:px-6">
        <div className="flex items-center gap-6 sm:gap-8">
          <Link href="/" className="flex items-center gap-2">
            <span className="inline-block h-5 w-5 rounded-[5px] bg-primary" />
            <span className="font-display text-lg tracking-tight text-ink">Laufwise</span>
          </Link>
          <nav className="flex items-center gap-5 text-sm">
            {tabs.map((t) => (
              <Link
                key={t.key}
                href={t.href}
                className={
                  t.key === active ? "font-medium text-ink" : "text-muted-foreground hover:text-ink"
                }
              >
                {t.label}
              </Link>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-3">
          <OrganizationSwitcher hidePersonal afterSelectOrganizationUrl="/studio" />
          <UserButton />
        </div>
      </div>
    </header>
  );
}
