import {
  BattleRecord,
  BattleRecordInput,
  BattleSessionState,
  BattleSessionStateInput,
  BattleStorageMode,
  BattleStatus,
  ParticipantType,
} from "./types";

type SupabaseRow = {
  id?: string;
  session_id: string;
  participant_id: string;
  participant_name: string;
  participant_type: ParticipantType;
  scenario_id: string;
  scenario_label?: string | null;
  status: BattleStatus;
  duration_seconds?: number | null;
  reason?: string | null;
  artifact_url?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string;
  updated_at?: string;
};

type SupabaseSessionStateRow = {
  session_id: string;
  scenario_id?: string | null;
  scenario_label?: string | null;
  human_started_at?: string | null;
  updated_at?: string | null;
};

const MAX_CLIENT_INLINE_IMAGE_DATA_URL_LENGTH = 360_000;
const PREPARED_HUMAN_STARTED_AT = "1970-01-01T00:00:00.000Z";
const INLINE_IMAGE_METADATA_KEYS = ["evidenceImageDataUrl", "screenshotDataUrl"];

declare global {
  var __gaiaBattleRecords: BattleRecord[] | undefined;
  var __gaiaBattleSessionStates: BattleSessionState[] | undefined;
}

function nowIso() {
  return new Date().toISOString();
}

function slug(value: string, fallback: string) {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9가-힣_-]+/gi, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return normalized || fallback;
}

function normalizeStatus(value: unknown): BattleStatus {
  const raw = String(value || "").trim().toUpperCase();
  if (raw === "PASS" || raw === "PASSED") return "SUCCESS";
  if (raw === "BLOCKED_USER_ACTION") return "BLOCKED";
  if (raw === "FAIL" || raw === "FAILED" || raw === "ERROR") return "FAIL";
  if (raw === "RUNNING") return "RUNNING";
  return "SUCCESS";
}

function sanitizeMetadata(metadata: Record<string, unknown> | null | undefined) {
  const clean = metadata && typeof metadata === "object" ? { ...metadata } : {};
  for (const key of INLINE_IMAGE_METADATA_KEYS) {
    const value = clean[key];
    if (typeof value === "string" && value.length > MAX_CLIENT_INLINE_IMAGE_DATA_URL_LENGTH) {
      delete clean[key];
      clean[`${key}SkippedReason`] = `inline_image_too_large:${value.length}`;
    }
  }
  if (Array.isArray(clean.evidenceImages)) {
    clean.evidenceImages = clean.evidenceImages
      .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
      .map((item) => {
        const image = { ...item };
        const dataUrl = image.dataUrl;
        if (typeof dataUrl === "string" && dataUrl.length > MAX_CLIENT_INLINE_IMAGE_DATA_URL_LENGTH) {
          delete image.dataUrl;
          image.skippedReason = `inline_image_too_large:${dataUrl.length}`;
        }
        return image;
      });
  }
  return clean;
}

function normalizeRecord(input: BattleRecordInput): BattleRecord {
  const createdAt = nowIso();
  const sessionId = String(input.sessionId || "").trim();
  const participantType = input.participantType || "human";
  const participantName = String(input.participantName || (participantType === "gaia" ? "GAIA" : "Human")).trim();
  const scenarioId = slug(String(input.scenarioId || "live-mission"), "live-mission");
  return {
    id: `${sessionId}:${slug(participantName, participantType)}:${scenarioId}`,
    sessionId,
    participantId: slug(String(input.participantId || participantName), participantType),
    participantName,
    participantType,
    scenarioId,
    scenarioLabel: String(input.scenarioLabel || scenarioId).trim(),
    status: normalizeStatus(input.status),
    durationSeconds:
      typeof input.durationSeconds === "number" && Number.isFinite(input.durationSeconds)
        ? Math.max(0, Math.round(input.durationSeconds * 100) / 100)
        : null,
    reason: String(input.reason || "").trim(),
    artifactUrl: String(input.artifactUrl || "").trim(),
    metadata: sanitizeMetadata(input.metadata),
    createdAt,
    updatedAt: createdAt,
  };
}

function normalizeSessionState(input: BattleSessionStateInput): BattleSessionState {
  const sessionId = String(input.sessionId || "").trim();
  const scenarioId = slug(String(input.scenarioId || "live-mission"), "live-mission");
  const now = nowIso();
  const hasHumanStartedAt = Object.prototype.hasOwnProperty.call(input, "humanStartedAt");
  return {
    sessionId,
    scenarioId,
    scenarioLabel: String(input.scenarioLabel || "Live QA Mission").trim(),
    humanStartedAt: hasHumanStartedAt ? String(input.humanStartedAt || "").trim() || PREPARED_HUMAN_STARTED_AT : now,
    updatedAt: now,
  };
}

function getSupabaseConfig() {
  const url = process.env.SUPABASE_URL?.replace(/\/$/, "");
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  const recordTable = process.env.SUPABASE_BATTLE_TABLE || "battle_records";
  const stateTable = process.env.SUPABASE_BATTLE_STATE_TABLE || "battle_session_states";
  if (!url || !key) return null;
  return { url, key, recordTable, stateTable };
}

export function getBattleStorageMode(): BattleStorageMode {
  return getSupabaseConfig() ? "supabase" : "memory";
}

function toRow(record: BattleRecord): SupabaseRow {
  return {
    session_id: record.sessionId,
    participant_id: record.participantId,
    participant_name: record.participantName,
    participant_type: record.participantType,
    scenario_id: record.scenarioId,
    scenario_label: record.scenarioLabel,
    status: record.status,
    duration_seconds: record.durationSeconds,
    reason: record.reason,
    artifact_url: record.artifactUrl,
    metadata: record.metadata,
    updated_at: record.updatedAt,
  };
}

function fromRow(row: SupabaseRow): BattleRecord {
  const createdAt = row.created_at || nowIso();
  return {
    id: row.id || `${row.session_id}:${row.participant_id}:${row.scenario_id}`,
    sessionId: row.session_id,
    participantId: row.participant_id,
    participantName: row.participant_name,
    participantType: row.participant_type,
    scenarioId: row.scenario_id,
    scenarioLabel: row.scenario_label || row.scenario_id,
    status: normalizeStatus(row.status),
    durationSeconds: typeof row.duration_seconds === "number" ? row.duration_seconds : null,
    reason: row.reason || "",
    artifactUrl: row.artifact_url || "",
    metadata: sanitizeMetadata(row.metadata),
    createdAt,
    updatedAt: row.updated_at || createdAt,
  };
}

function toSessionStateRow(state: BattleSessionState): SupabaseSessionStateRow {
  return {
    session_id: state.sessionId,
    scenario_id: state.scenarioId,
    scenario_label: state.scenarioLabel,
    human_started_at: state.humanStartedAt || PREPARED_HUMAN_STARTED_AT,
    updated_at: state.updatedAt,
  };
}

function fromSessionStateRow(row: SupabaseSessionStateRow): BattleSessionState {
  return {
    sessionId: row.session_id,
    scenarioId: row.scenario_id || "live-mission",
    scenarioLabel: row.scenario_label || "Live QA Mission",
    humanStartedAt: row.human_started_at || "",
    updatedAt: row.updated_at || row.human_started_at || nowIso(),
  };
}

async function supabaseFetch(path: string, init?: RequestInit) {
  const config = getSupabaseConfig();
  if (!config) throw new Error("Supabase is not configured");
  const response = await fetch(`${config.url}/rest/v1/${path}`, {
    ...init,
    headers: {
      apikey: config.key,
      authorization: `Bearer ${config.key}`,
      "content-type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Supabase request failed (${response.status}): ${detail}`);
  }
  return response;
}

function memoryRecords() {
  globalThis.__gaiaBattleRecords ||= [];
  return globalThis.__gaiaBattleRecords;
}

function memorySessionStates() {
  globalThis.__gaiaBattleSessionStates ||= [];
  return globalThis.__gaiaBattleSessionStates;
}

export async function listBattleRecords(sessionId: string): Promise<BattleRecord[]> {
  const cleanSessionId = String(sessionId || "").trim();
  if (!cleanSessionId) return [];
  const config = getSupabaseConfig();
  if (config) {
    const query = new URLSearchParams({
      session_id: `eq.${cleanSessionId}`,
      select: "*",
      order: "updated_at.desc",
    });
    const response = await supabaseFetch(`${config.recordTable}?${query.toString()}`);
    const rows = (await response.json()) as SupabaseRow[];
    return rows.map(fromRow);
  }
  return memoryRecords()
    .filter((record) => record.sessionId === cleanSessionId)
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

export async function upsertBattleRecord(input: BattleRecordInput): Promise<BattleRecord> {
  const record = normalizeRecord(input);
  if (!record.sessionId) {
    throw new Error("sessionId is required");
  }
  const config = getSupabaseConfig();
  if (config) {
    const response = await supabaseFetch(
      `${config.recordTable}?on_conflict=session_id,participant_id,scenario_id`,
      {
        method: "POST",
        headers: { Prefer: "resolution=merge-duplicates,return=representation" },
        body: JSON.stringify(toRow(record)),
      },
    );
    const rows = (await response.json()) as SupabaseRow[];
    return fromRow(rows[0]);
  }
  const records = memoryRecords();
  const index = records.findIndex(
    (item) =>
      item.sessionId === record.sessionId &&
      item.participantId === record.participantId &&
      item.scenarioId === record.scenarioId,
  );
  if (index >= 0) {
    records[index] = { ...records[index], ...record, createdAt: records[index].createdAt, updatedAt: nowIso() };
    return records[index];
  }
  records.push(record);
  return record;
}

export async function getBattleSessionState(sessionId: string): Promise<BattleSessionState | null> {
  const cleanSessionId = String(sessionId || "").trim();
  if (!cleanSessionId) return null;
  const config = getSupabaseConfig();
  if (config) {
    const query = new URLSearchParams({
      session_id: `eq.${cleanSessionId}`,
      select: "*",
      limit: "1",
    });
    const response = await supabaseFetch(`${config.stateTable}?${query.toString()}`);
    const rows = (await response.json()) as SupabaseSessionStateRow[];
    return rows[0] ? fromSessionStateRow(rows[0]) : null;
  }
  return memorySessionStates().find((state) => state.sessionId === cleanSessionId) || null;
}

export async function updateBattleSessionState(input: BattleSessionStateInput): Promise<BattleSessionState> {
  const state = normalizeSessionState(input);
  if (!state.sessionId) {
    throw new Error("sessionId is required");
  }
  const config = getSupabaseConfig();
  if (config) {
    const response = await supabaseFetch(`${config.stateTable}?on_conflict=session_id`, {
      method: "POST",
      headers: { Prefer: "resolution=merge-duplicates,return=representation" },
      body: JSON.stringify(toSessionStateRow(state)),
    });
    const rows = (await response.json()) as SupabaseSessionStateRow[];
    return fromSessionStateRow(rows[0]);
  }
  const states = memorySessionStates();
  const index = states.findIndex((item) => item.sessionId === state.sessionId);
  if (index >= 0) {
    states[index] = state;
    return state;
  }
  states.push(state);
  return state;
}

export async function resetBattleTimer(sessionId: string): Promise<{ sessionId: string; storage: BattleStorageMode }> {
  const cleanSessionId = String(sessionId || "").trim();
  if (!cleanSessionId) {
    throw new Error("sessionId is required");
  }
  const config = getSupabaseConfig();
  if (config) {
    const query = new URLSearchParams({ session_id: `eq.${cleanSessionId}` });
    await supabaseFetch(`${config.stateTable}?${query.toString()}`, {
      method: "DELETE",
      headers: { Prefer: "return=minimal" },
    });
    return { sessionId: cleanSessionId, storage: "supabase" };
  }

  globalThis.__gaiaBattleSessionStates = memorySessionStates().filter((state) => state.sessionId !== cleanSessionId);
  return { sessionId: cleanSessionId, storage: "memory" };
}

export async function resetBattleSession(sessionId: string): Promise<{ sessionId: string; storage: BattleStorageMode }> {
  const cleanSessionId = String(sessionId || "").trim();
  if (!cleanSessionId) {
    throw new Error("sessionId is required");
  }
  const config = getSupabaseConfig();
  if (config) {
    const query = new URLSearchParams({ session_id: `eq.${cleanSessionId}` });
    await supabaseFetch(`${config.recordTable}?${query.toString()}`, {
      method: "DELETE",
      headers: { Prefer: "return=minimal" },
    });
    await supabaseFetch(`${config.stateTable}?${query.toString()}`, {
      method: "DELETE",
      headers: { Prefer: "return=minimal" },
    });
    return { sessionId: cleanSessionId, storage: "supabase" };
  }

  globalThis.__gaiaBattleRecords = memoryRecords().filter((record) => record.sessionId !== cleanSessionId);
  globalThis.__gaiaBattleSessionStates = memorySessionStates().filter((state) => state.sessionId !== cleanSessionId);
  return { sessionId: cleanSessionId, storage: "memory" };
}

export async function deleteBattleRecord(
  sessionId: string,
  recordId: string,
): Promise<{ sessionId: string; recordId: string; storage: BattleStorageMode }> {
  const cleanSessionId = String(sessionId || "").trim();
  const cleanRecordId = String(recordId || "").trim();
  if (!cleanSessionId) {
    throw new Error("sessionId is required");
  }
  if (!cleanRecordId) {
    throw new Error("recordId is required");
  }
  const config = getSupabaseConfig();
  if (config) {
    const query = new URLSearchParams({
      session_id: `eq.${cleanSessionId}`,
      id: `eq.${cleanRecordId}`,
    });
    await supabaseFetch(`${config.recordTable}?${query.toString()}`, {
      method: "DELETE",
      headers: { Prefer: "return=minimal" },
    });
    return { sessionId: cleanSessionId, recordId: cleanRecordId, storage: "supabase" };
  }

  globalThis.__gaiaBattleRecords = memoryRecords().filter(
    (record) => !(record.sessionId === cleanSessionId && record.id === cleanRecordId),
  );
  return { sessionId: cleanSessionId, recordId: cleanRecordId, storage: "memory" };
}
