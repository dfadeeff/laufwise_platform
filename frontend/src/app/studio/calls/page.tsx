import { redirect } from "next/navigation";
// Calls moved into Run history. Deep links from tests and follow-ups still land on the call.
export default async function CallsPage({
  searchParams,
}: {
  searchParams: Promise<{ call?: string }>;
}) {
  const { call } = await searchParams;
  redirect(`/studio/history${call ? `?call=${encodeURIComponent(call)}` : ""}`);
}
