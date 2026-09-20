import { redirect } from "next/navigation";
// Workflows are a kind of agent now, listed with the rest.
export default function WorkflowsPage() {
  redirect("/studio");
}
