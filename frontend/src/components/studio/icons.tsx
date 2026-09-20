// The Studio's icon set: 24-grid stroke outlines, drawn inline.
//
// Inline because an icon package is permanent code we do not control for what amounts to a
// dozen paths, and because these are load-bearing at 13–17px — the stroke width is tuned to
// the rail's text, which a generic set would not be.

const PATHS = {
  // Workspace rail
  cube: (
    <>
      <path d="M12 2.6 20 7v10l-8 4.4L4 17V7z" />
      <path d="M12 12v9.4" />
      <path d="m4 7 8 5 8-5" />
    </>
  ),
  activity: <path d="M3 12h4l2.5 6 5-14 2.5 8h4" />,
  bars: (
    <>
      <rect x="3" y="3" width="18" height="18" rx="3" />
      <path d="M8 16v-4" />
      <path d="M12 16V8" />
      <path d="M16 16v-6" />
    </>
  ),
  shield: (
    <>
      <path d="M12 2.7 20 6v6c0 4.4-3.2 8-8 9.3C7.2 20 4 16.4 4 12V6z" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),

  // Chrome
  menu: (
    <>
      <path d="M4 7h16" />
      <path d="M4 12h16" />
      <path d="M4 17h16" />
    </>
  ),
  chevronDown: <polyline points="6 9 12 15 18 9" />,
  chevronRight: <polyline points="9 6 15 12 9 18" />,
  check: <polyline points="20 6 9 17 4 12" />,
  plus: (
    <>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </>
  ),
  arrowRight: (
    <>
      <path d="M5 12h13" />
      <polyline points="12 6 18 12 12 18" />
    </>
  ),

  // Domain
  phone: (
    <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.8a16 16 0 0 0 6 6l1.2-1.2a2 2 0 0 1 2.1-.5c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.8 2.1z" />
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <polyline points="12 7 12 12 15.5 14" />
    </>
  ),
  swap: (
    <>
      <polyline points="16 3 20 7 16 11" />
      <path d="M20 7H4" />
      <polyline points="8 13 4 17 8 21" />
      <path d="M4 17h16" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.6-3.6" />
    </>
  ),
  alert: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5v5" />
      <path d="M12 16.2v.2" />
    </>
  ),
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({
  name,
  size = 17,
  width = 1.8,
  className,
}: {
  name: IconName;
  size?: number;
  width?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={width}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={`shrink-0 ${className ?? ""}`}
    >
      {PATHS[name]}
    </svg>
  );
}
