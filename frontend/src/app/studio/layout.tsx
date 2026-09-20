import type { ReactNode } from "react";
import { WorkspaceShell } from "@/components/studio/WorkspaceShell";
export default function StudioLayout({ children }: { children: ReactNode }) {
  return <WorkspaceShell>{children}</WorkspaceShell>;
}
