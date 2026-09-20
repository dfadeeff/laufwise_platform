import { notFound } from "next/navigation";
export default async function AgentPage({
  params,
}: {
  params: Promise<{ agentId: string; section?: string[] }>;
}) {
  const { agentId, section } = await params;
  const name = section?.[0] ?? "overview";
  if (
    (section?.length ?? 0) > 1 ||
    ![
      "overview",
      "instructions",
      "knowledge",
      "capabilities",
      "voice",
      "tests",
      "phone",
      "history",
    ].includes(name)
  )
    notFound();
  return null;
}
