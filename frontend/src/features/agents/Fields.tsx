"use client";
import { useId, useState, type ReactNode } from "react";
import { Icon } from "@/components/studio/icons";

/** A block of the editor: a white card with a title, an optional count on the right, and a body
 *  that folds away. Practice knowledge is four of these stacked, and reading the fourth means
 *  being able to put the first three out of sight. */
export function Section({
  title,
  description,
  meta,
  collapsible = true,
  defaultOpen = true,
  children,
}: {
  title: string;
  description?: string;
  /** A count or status for the right of the header — "3 treatments", "Mo–Fr". */
  meta?: string;
  collapsible?: boolean;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = useId();
  const shown = !collapsible || open;
  const header = (
    <>
      <div className="min-w-0 text-left">
        <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
        {description && (
          <p className="mt-1 text-[13px] leading-6 text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2.5 pt-0.5">
        {meta && (
          <span className="text-xs text-muted-foreground">{meta}</span>
        )}
        {collapsible && (
          <span className={`text-faint transition ${shown ? "" : "-rotate-90"}`}>
            <Icon name="chevronDown" size={15} width={2.2} />
          </span>
        )}
      </div>
    </>
  );
  return (
    <section className="rounded-xl border border-border bg-white">
      {collapsible ? (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-controls={bodyId}
          className="flex w-full items-start justify-between gap-4 px-5 py-4 sm:px-6"
        >
          {header}
        </button>
      ) : (
        <div className="flex items-start justify-between gap-4 px-5 py-4 sm:px-6">
          {header}
        </div>
      )}
      {shown && (
        <div
          id={bodyId}
          className="space-y-5 border-t border-border px-5 py-5 sm:px-6"
        >
          {children}
        </div>
      )}
    </section>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="studio-label">{label}</span>
      {hint && (
        <span className="mt-1 block text-xs leading-5 text-muted-foreground">
          {hint}
        </span>
      )}
      <span className="mt-2 block">{children}</span>
    </label>
  );
}
