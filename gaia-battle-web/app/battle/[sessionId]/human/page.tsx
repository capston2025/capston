import { HumanSubmitClient } from "@/components/HumanSubmitClient";
import { listBattleRecords } from "@/lib/store";

type PageProps = {
  params: Promise<{ sessionId: string }>;
  searchParams: Promise<{ scenarioId?: string }>;
};

export default async function HumanPage({ params, searchParams }: PageProps) {
  const { sessionId } = await params;
  const { scenarioId } = await searchParams;
  const records = await listBattleRecords(sessionId);
  return <HumanSubmitClient sessionId={sessionId} scenarioId={scenarioId || "live-mission"} initialRecords={records} />;
}
