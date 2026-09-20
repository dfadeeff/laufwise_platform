import { redirect } from "next/navigation";
// Workflow runs moved into Run history.
export default function RunsPage() {
  redirect("/studio/history?tab=runs");
}
