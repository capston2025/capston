import { BattleRecord, BattleSummary } from "./types";

function bestSeconds(records: BattleRecord[]) {
  const values = records
    .filter((record) => record.status === "SUCCESS")
    .map((record) => record.durationSeconds)
    .filter((value): value is number => typeof value === "number");
  if (!values.length) return null;
  return Math.min(...values);
}

export function summarizeBattle(records: BattleRecord[]): BattleSummary {
  return {
    total: records.length,
    humanTotal: records.filter((record) => record.participantType === "human").length,
    gaiaTotal: records.filter((record) => record.participantType === "gaia").length,
    successTotal: records.filter((record) => record.status === "SUCCESS").length,
    failTotal: records.filter((record) => record.status === "FAIL").length,
    blockedTotal: records.filter((record) => record.status === "BLOCKED").length,
    bestHumanSeconds: bestSeconds(records.filter((record) => record.participantType === "human")),
    bestGaiaSeconds: bestSeconds(records.filter((record) => record.participantType === "gaia")),
  };
}
