import { BattleRecord, BattleSummary } from "./types";

function bestSeconds(records: BattleRecord[]) {
  const values = records
    .filter((record) => record.status === "SUCCESS")
    .map((record) => record.durationSeconds)
    .filter((value): value is number => typeof value === "number");
  if (!values.length) return null;
  return Math.min(...values);
}

function averageSeconds(records: BattleRecord[]) {
  const values = records
    .filter((record) => record.status === "SUCCESS")
    .map((record) => record.durationSeconds)
    .filter((value): value is number => typeof value === "number");
  if (!values.length) return null;
  const total = values.reduce((sum, value) => sum + value, 0);
  return Math.round((total / values.length) * 100) / 100;
}

export function summarizeBattle(records: BattleRecord[]): BattleSummary {
  const humanRecords = records.filter((record) => record.participantType === "human");
  const gaiaRecords = records.filter((record) => record.participantType === "gaia");
  return {
    total: records.length,
    humanTotal: humanRecords.length,
    gaiaTotal: gaiaRecords.length,
    successTotal: records.filter((record) => record.status === "SUCCESS").length,
    failTotal: records.filter((record) => record.status === "FAIL").length,
    blockedTotal: records.filter((record) => record.status === "BLOCKED").length,
    averageHumanSeconds: averageSeconds(humanRecords),
    averageGaiaSeconds: averageSeconds(gaiaRecords),
    bestHumanSeconds: bestSeconds(humanRecords),
    bestGaiaSeconds: bestSeconds(gaiaRecords),
  };
}
