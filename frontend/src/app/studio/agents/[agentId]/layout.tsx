import { AgentWorkspace } from "@/features/agents/AgentWorkspace";
export default async function AgentLayout({
  params,
  children,
}: {
  params: Promise<{ agentId: string }>;
  children: React.ReactNode;
}) {
  const { agentId } = await params;
  return (
    <>
      <AgentWorkspace agentId={agentId} />
      {children}
    </>
  );
}
