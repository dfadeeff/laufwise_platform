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
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import type { WorkspaceModules } from "@/types";
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
      { href: "/studio/clients", title: "All workspaces", icon: "clients" as const },
    ],
  },
  {
    group: "Control",
    items: [
      { href: "/studio/governance", title: "Governance", icon: "shield" as const },
    ],
  },
];

// What the workspace runs (ADR-0028), read once by the shell and shared with every page in it, so
// a page never asks again and the rail and the page can never disagree. Re-read when the
// organization changes: each one is its own workspace.
interface ModulesState {
  modules: WorkspaceModules | null;
  error: string;
  choose: (key: string) => Promise<void>;
}

const ModulesContext = createContext<ModulesState>({
  modules: null,
  error: "",
  choose: async () => {},
});

export const useWorkspaceModules = () => useContext(ModulesContext);

// Said when the workspace runs no module, or several: nothing here may speak for one vertical.
const KERNEL_PITCH = ["Built for your workspace", "Every action checked.", "Every run traceable."];

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
  // On a desktop the rail can fold to its icons, giving wide pages (a call's transcript) the
  // room. Remembered per browser; a blocked storage just means it starts unfolded.
  const [folded, setFolded] = useState(false);
  useEffect(() => {
    try {
      setFolded(localStorage.getItem("studio.rail") === "folded");
    } catch {}
  }, []);
  const fold = (value: boolean) => {
    setFolded(value);
    try {
      localStorage.setItem("studio.rail", value ? "folded" : "open");
    } catch {}
  };
  const [trail, setTrail] = useState<HTMLElement | null>(null);
  const [actions, setActions] = useState<HTMLElement | null>(null);
  const practice = organization?.name ?? "Your workspace";
  const [modules, setModules] = useState<WorkspaceModules | null>(null);
  const [modulesError, setModulesError] = useState("");
  useEffect(() => {
    let current = true;
    setModules(null);
    setModulesError("");
    api
      .workspaceModules()
      .then((m) => current && setModules(m))
      .catch((e) => current && setModulesError(e instanceof Error ? e.message : String(e)));
    return () => {
      current = false;
    };
  }, [organization?.id]);
  const choose = async (key: string) => setModules(await api.addWorkspaceModule(key));
  const [title, ...lines] =
    modules?.running.length === 1 ? modules.running[0].pitch : KERNEL_PITCH;
  return (
    <ModulesContext.Provider value={{ modules, error: modulesError, choose }}>
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
          <button
            className="studio-nav -ml-1 hidden px-2 lg:flex"
            aria-expanded={!folded}
            aria-label={folded ? "Show navigation" : "Hide navigation"}
            title={folded ? "Show navigation" : "Hide navigation"}
            onClick={() => fold(!folded)}
          >
            <Icon name="menu" />
          </button>
          <Link
            href="/studio"
            className={`flex shrink-0 items-center gap-2.5 no-underline ${folded ? "" : "lg:w-[150px]"}`}
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
            className={`${open ? "block" : "hidden"} border-b border-border bg-surface lg:fixed lg:bottom-0 lg:top-14 lg:block lg:border-b-0 lg:border-r ${folded ? "lg:w-[60px]" : "lg:w-[212px]"}`}
          >
            <div className={`flex h-full flex-col gap-1 py-3.5 ${folded ? "px-3 lg:px-2" : "px-3"}`}>
              {NAVIGATION.map(({ group, items }) => (
                <div key={group} className="mb-1">
                  <p className={`studio-eyebrow px-2.5 pb-1.5 pt-2.5 ${folded ? "lg:sr-only" : ""}`}>
                    {group}
                  </p>
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
                          title={folded ? item.title : undefined}
                          className={`studio-nav no-underline ${folded ? "lg:justify-center" : ""} ${active ? "studio-nav-on" : ""}`}
                        >
                          <Icon name={item.icon} />
                          <span className={folded ? "lg:sr-only" : ""}>{item.title}</span>
                        </Link>
                      );
                    })}
                  </nav>
                </div>
              ))}
              <div className={`mt-auto hidden border-t border-border px-2.5 pt-4 ${folded ? "" : "lg:block"}`}>
                <p className="text-xs font-medium text-ink">{title}</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {lines.map((line) => (
                    <span key={line} className="block">
                      {line}
                    </span>
                  ))}
                </p>
              </div>
            </div>
          </aside>
          <div className={`min-w-0 flex-1 ${folded ? "lg:ml-[60px]" : "lg:ml-[212px]"}`}>
            <div key={organization?.id ?? "no-organization"}>{children}</div>
          </div>
        </div>
      </div>
    </SlotContext.Provider>
    </ModulesContext.Provider>
  );
}
