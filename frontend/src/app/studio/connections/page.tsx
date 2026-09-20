import { redirect } from "next/navigation";
// Connections live under Governance: they are what the engine checks a write against.
export default function ConnectionsPage() {
  redirect("/studio/governance/connections");
}
