import { BattleRecord } from "./types";

export type CaseVerdict =
  | "HUMAN_FASTER"
  | "GAIA_FASTER"
  | "TIE"
  | "HUMAN_WIN"
  | "GAIA_WIN"
  | "BOTH_FAILED"
  | "WAITING";

export type BattleCase = {
  scenarioId: string;
  label: string;
  humans: BattleRecord[];
  gaias: BattleRecord[];
  bestHuman: BattleRecord | null;
  bestGaia: BattleRecord | null;
  verdict: CaseVerdict;
  latestAt: string;
};

function metadataText(value: unknown) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

export function isDemoLikeRecord(record: BattleRecord) {
  const joined = [
    record.sessionId,
    record.scenarioId,
    record.scenarioLabel,
    record.reason,
    metadataText(record.metadata?.evidenceSource),
    metadataText(record.metadata?.suiteId),
  ]
    .join(" ")
    .toLowerCase();
  return (
    joined.includes("smoke") ||
    joined.includes("evidence-demo") ||
    joined.includes("remote smoke") ||
    joined.includes("원격 저장 검증") ||
    joined.includes("cli 타이머 연동 검증")
  );
}

function pickRepresentative(records: BattleRecord[]): BattleRecord | null {
  if (!records.length) return null;
  const successes = records.filter(
    (record) => record.status === "SUCCESS" && typeof record.durationSeconds === "number",
  );
  if (successes.length) {
    return successes.reduce((best, record) =>
      (record.durationSeconds as number) < (best.durationSeconds as number) ? record : best,
    );
  }
  return [...records].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))[0];
}

function decideVerdict(human: BattleRecord | null, gaia: BattleRecord | null): CaseVerdict {
  const humanWon = human?.status === "SUCCESS";
  const gaiaWon = gaia?.status === "SUCCESS";
  if (humanWon && gaiaWon) {
    const humanSeconds = human?.durationSeconds ?? Number.POSITIVE_INFINITY;
    const gaiaSeconds = gaia?.durationSeconds ?? Number.POSITIVE_INFINITY;
    if (humanSeconds < gaiaSeconds) return "HUMAN_FASTER";
    if (gaiaSeconds < humanSeconds) return "GAIA_FASTER";
    return "TIE";
  }
  if (humanWon) return "HUMAN_WIN";
  if (gaiaWon) return "GAIA_WIN";
  if (human && gaia) return "BOTH_FAILED";
  return "WAITING";
}

// Groups records into one battle case per scenarioId so the same case from the
// human side and the GAIA side line up 1:1 (GAIA's start signal seeds the human
// timer with the same scenarioId, so both submit under one key).
export function groupBattleCases(records: BattleRecord[]): BattleCase[] {
  const buckets = new Map<string, BattleRecord[]>();
  for (const record of records.filter((entry) => !isDemoLikeRecord(entry))) {
    const key = record.scenarioId || "live-mission";
    buckets.set(key, [...(buckets.get(key) || []), record]);
  }

  const cases = [...buckets.entries()].map(([scenarioId, group]) => {
    const humans = group.filter((record) => record.participantType === "human");
    const gaias = group.filter((record) => record.participantType === "gaia");
    const bestHuman = pickRepresentative(humans);
    const bestGaia = pickRepresentative(gaias);
    const label =
      gaias.find((record) => record.scenarioLabel)?.scenarioLabel ||
      humans.find((record) => record.scenarioLabel)?.scenarioLabel ||
      scenarioId;
    const latestAt = group.reduce((latest, record) => (record.updatedAt > latest ? record.updatedAt : latest), "");
    return {
      scenarioId,
      label,
      humans,
      gaias,
      bestHuman,
      bestGaia,
      verdict: decideVerdict(bestHuman, bestGaia),
      latestAt,
    };
  });

  return cases.sort((a, b) => b.latestAt.localeCompare(a.latestAt));
}
