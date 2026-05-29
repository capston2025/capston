import { BattleBoardClient } from "@/components/BattleBoardClient";
import { listBattleRecords } from "@/lib/store";

type PageProps = {
  params: Promise<{ sessionId: string }>;
};

export default async function BattlePage({ params }: PageProps) {
  const { sessionId } = await params;
  const records = await listBattleRecords(sessionId);
  return <BattleBoardClient sessionId={sessionId} initialRecords={records} />;
}
