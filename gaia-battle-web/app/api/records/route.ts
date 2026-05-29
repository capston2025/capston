import { NextRequest, NextResponse } from "next/server";

import { getBattleSessionState, getBattleStorageMode, listBattleRecords, upsertBattleRecord } from "@/lib/store";
import { BattleRecordInput } from "@/lib/types";

export const dynamic = "force-dynamic";

function unauthorized() {
  return NextResponse.json({ error: "invalid upload token" }, { status: 401 });
}

function tokenFrom(request: NextRequest) {
  const auth = request.headers.get("authorization") || "";
  if (auth.toLowerCase().startsWith("bearer ")) return auth.slice("bearer ".length).trim();
  return request.headers.get("x-battle-token") || "";
}

function requiresToken(input: BattleRecordInput) {
  return Boolean(process.env.BATTLE_UPLOAD_TOKEN) && input.participantType !== "human";
}

function roundedSeconds(startedAt: string, endedAt: Date) {
  const startMs = new Date(startedAt).getTime();
  if (Number.isNaN(startMs)) return null;
  return Math.max(0, Math.round(((endedAt.getTime() - startMs) / 1000) * 100) / 100);
}

async function withServerHumanDuration(input: BattleRecordInput): Promise<BattleRecordInput> {
  if (input.participantType !== "human") return input;
  const measuredAt = new Date();
  const state = await getBattleSessionState(input.sessionId || "");
  const durationSeconds = state?.humanStartedAt ? roundedSeconds(state.humanStartedAt, measuredAt) : null;
  if (durationSeconds === null) return input;
  return {
    ...input,
    durationSeconds,
    metadata: {
      ...(input.metadata || {}),
      clientDurationSeconds: input.durationSeconds ?? null,
      serverMeasuredAt: measuredAt.toISOString(),
      timingSource: "server",
    },
  };
}

export async function GET(request: NextRequest) {
  const sessionId = request.nextUrl.searchParams.get("sessionId") || "";
  const records = await listBattleRecords(sessionId);
  return NextResponse.json({
    records,
    storage: getBattleStorageMode(),
  });
}

export async function POST(request: NextRequest) {
  let input: BattleRecordInput;
  try {
    input = (await request.json()) as BattleRecordInput;
  } catch {
    return NextResponse.json({ error: "invalid json body" }, { status: 400 });
  }

  if (requiresToken(input) && tokenFrom(request) !== process.env.BATTLE_UPLOAD_TOKEN) {
    return unauthorized();
  }

  try {
    const record = await upsertBattleRecord(await withServerHumanDuration(input));
    const records = await listBattleRecords(record.sessionId);
    return NextResponse.json({ record, records });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "record upsert failed" },
      { status: 400 },
    );
  }
}
