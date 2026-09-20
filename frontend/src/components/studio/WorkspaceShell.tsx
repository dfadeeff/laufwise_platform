"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  OrganizationSwitcher,
  UserButton,
  useOrganization,
} from "@clerk/nextjs";
import {
  createContext,
  useContext,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Icon } from "./icons";

// The workspace chrome: one top bar across the whole width, one rail down the left, and the
// page in between. Both are fixed points — whatever you are editing, the practice you are in
// and the way out are in the same place.
//
// The four things a practice owner does here: build agents, read what they did, see how it is
// going, and check what the platform enforced. Everything else lives inside one of them.
const NAVIGATION = [
  {
    group: "Workspace",
    items: [
      { href: "/studio", title: "Agents", icon: "cube" as const },
      { href: "/studio/history", title: "Run history", icon: "activity" as const },
      { href: "/studio/dashboard", title: "Dashboard", icon: "bars" as const },
    ],
  },
  {
    group: "Control",
    items: [
      { href: "/studio/governance", title: "Governance", icon: "shield" as const },
    ],
  },
];

/** A step in the top bar's breadcrumb. The last one is the page you are on and is not a link. */
export interface Crumb {
  label: string;
  href?: string;
}

// Two slots in the top bar that any page can fill: the breadcrumb tail after the practice, and
// the actions on the right. They are DOM portals rather than lifted state on purpose — a page's
// Save/Publish handlers close over its current draft, and a portal re-renders with the page, so
// the button in the bar can never act on a stale closure the way an effect-synced node would.
const SlotContext = createContext<{
  trail: HTMLElement | null;
  actions: HTMLElement | null;
}>({ trail: null, actions: null });

function Slot({ which, children }: { which: "trail" | "actions"; children: ReactNode }) {
  const node = useContext(SlotContext)[which];
  return node ? createPortal(children, node) : null;
}

/** Breadcrumb steps shown after the practice name. Render it anywhere inside the Studio. */
export function StudioTrail({ crumbs }: { crumbs: Crumb[] }) {
  return (
    <Slot which="trail">
      {crumbs.map((crumb, i) => (
        <span key={`${crumb.label}-${i}`} className="flex items-center gap-2">
          <span aria-hidden className="text-faint">
            /
          </span>
          {crumb.href ? (
            <Link
              href={crumb.href}
              className="font-medium text-muted-foreground no-underline hover:text-ink"
            >
              {crumb.label}
            </Link>
          ) : (
            <span className="max-w-[18ch] truncate font-semibold text-ink lg:max-w-none">
              {crumb.label}
            </span>
          )}
        </span>
      ))}
    </Slot>
  );
}

/** The page's own actions, in the one top bar. Save/Test/Publish belong here, not in a second
 *  sticky header stacked under the first. */
export function StudioActions({ children }: { children: ReactNode }) {
  return <Slot which="actions">{children}</Slot>;
}

export function WorkspaceShell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { organization } = useOrganization();
  const [open, setOpen] = useState(false);
  const [trail, setTrail] = useState<HTMLElement | null>(null);
  const [actions, setActions] = useState<HTMLElement | null>(null);
  const practice = organization?.name ?? "Your workspace";
  return (
    <SlotContext.Provider value={{ trail, actions }}>
      <div className="min-h-screen bg-background text-foreground">
        <header className="fixed inset-x-0 top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-white px-3 sm:px-4">
          <button
            className="studio-nav -ml-1 px-2 lg:hidden"
            aria-expanded={open}
            aria-label="Toggle navigation"
            onClick={() => setOpen(!open)}
          >
            <Icon name="menu" />
          </button>
          <Link
            href="/studio"
            className="flex shrink-0 items-center gap-2.5 no-underline lg:w-[188px]"
          >
            <span className="grid h-7 w-7 place-items-center rounded-[9px] bg-primary text-[15px] font-bold text-white">
              L
            </span>
            {/* On a phone the bar carries the page's Save/Test/Publish and the mark is enough
                to get home; the wordmark is the first thing that can afford to go. */}
            <span className="hidden font-display text-base text-ink sm:inline">
              Laufwise
            </span>
          </Link>
          <span aria-hidden className="hidden h-5 w-px bg-border md:block" />
          <nav
            aria-label="Breadcrumb"
            className="hidden min-w-0 items-center gap-2 text-[13px] md:flex"
          >
            <span className="flex h-7 items-center gap-1.5 rounded-lg bg-muted px-2 font-medium text-[#4A525E]">
              <span className="grid h-4 w-4 place-items-center rounded-[5px] bg-accent text-[10px] font-bold text-primary">
                {practice.slice(0, 1).toUpperCase()}
              </span>
              <span className="max-w-[16ch] truncate">{practice}</span>
            </span>
            <span ref={setTrail} className="flex min-w-0 items-center gap-2" />
          </nav>
          <div className="min-w-0 flex-1" />
          <div
            ref={setActions}
            className="flex min-w-0 shrink items-center gap-2 overflow-x-auto"
          />
          <span aria-hidden className="hidden h-5 w-px bg-border sm:block" />
          <div className="flex shrink-0 items-center gap-2">
            <div className="hidden sm:block">
              <OrganizationSwitcher
                hidePersonal
                afterSelectOrganizationUrl="/studio"
              />
            </div>
            <UserButton />
          </div>
        </header>

        <div className="pt-14 lg:flex">
          <aside
            className={`${open ? "block" : "hidden"} border-b border-border bg-surface lg:fixed lg:bottom-0 lg:top-14 lg:block lg:w-[212px] lg:border-b-0 lg:border-r`}
          >
            <div className="flex h-full flex-col gap-1 px-3 py-3.5">
              {NAVIGATION.map(({ group, items }) => (
                <div key={group} className="mb-1">
                  <p className="studio-eyebrow px-2.5 pb-1.5 pt-2.5">{group}</p>
                  <nav aria-label={group} className="space-y-0.5">
                    {items.map((item) => {
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
                          className={`studio-nav no-underline ${active ? "studio-nav-on" : ""}`}
                        >
                          <Icon name={item.icon} />
                          {item.title}
                        </Link>
                      );
                    })}
                  </nav>
                </div>
              ))}
              <div className="mt-auto hidden border-t border-border px-2.5 pt-4 lg:block">
                <p className="text-xs font-medium text-ink">
                  Built for your practice
                </p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  Every booking checked.
                  <br />
                  Every conversation traceable.
                </p>
              </div>
            </div>
          </aside>
          <div className="min-w-0 flex-1 lg:ml-[212px]">
            <div key={organization?.id ?? "no-organization"}>{children}</div>
          </div>
        </div>
      </div>
    </SlotContext.Provider>
  );
}
