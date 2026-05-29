import { HumanSubmitClient } from "@/components/HumanSubmitClient";
import { listBattleRecords } from "@/lib/store";

export const dynamic = "force-dynamic";

const defaultSessionId = process.env.NEXT_PUBLIC_DEFAULT_SESSION_ID || "battle-live";

export default async function Home() {
  const records = await listBattleRecords(defaultSessionId);
  return <HumanSubmitClient sessionId={defaultSessionId} scenarioId="live-mission" initialRecords={records} />;
}
