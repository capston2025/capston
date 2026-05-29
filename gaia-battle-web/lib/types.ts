export type ParticipantType = "gaia" | "human";
export type BattleStatus = "SUCCESS" | "FAIL" | "BLOCKED" | "RUNNING";
export type BattleStorageMode = "supabase" | "memory";

export type BattleRecord = {
  id: string;
  sessionId: string;
  participantId: string;
  participantName: string;
  participantType: ParticipantType;
  scenarioId: string;
  scenarioLabel: string;
  status: BattleStatus;
  durationSeconds: number | null;
  reason: string;
  artifactUrl: string;
  metadata: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
};

export type BattleRecordInput = {
  sessionId: string;
  participantId?: string;
  participantName?: string;
  participantType?: ParticipantType;
  scenarioId?: string;
  scenarioLabel?: string;
  status?: BattleStatus;
  durationSeconds?: number | null;
  reason?: string;
  artifactUrl?: string;
  metadata?: Record<string, unknown>;
};

export type BattleSessionState = {
  sessionId: string;
  scenarioId: string;
  scenarioLabel: string;
  humanStartedAt: string;
  updatedAt: string;
};

export type BattleSessionStateInput = {
  sessionId: string;
  scenarioId?: string;
  scenarioLabel?: string;
  humanStartedAt?: string;
};

export type BattleSummary = {
  total: number;
  humanTotal: number;
  gaiaTotal: number;
  successTotal: number;
  failTotal: number;
  blockedTotal: number;
  bestHumanSeconds: number | null;
  bestGaiaSeconds: number | null;
};
