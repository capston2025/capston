import { NextRequest, NextResponse } from "next/server";

import {
  getBattleSessionState,
  getBattleStorageMode,
  resetBattleSession,
  resetBattleTimer,
  updateBattleSessionState,
} from "@/lib/store";
import { BattleSessionStateInput } from "@/lib/types";

export const dynamic = "force-dynamic";

function tokenFrom(request: NextRequest) {
  const auth = request.headers.get("authorization") || "";
  if (auth.toLowerCase().startsWith("bearer ")) return auth.slice("bearer ".length).trim();
  return request.headers.get("x-battle-reset-token") || "";
}

function requiresResetToken() {
  return Boolean(process.env.BATTLE_RESET_TOKEN);
}

export async function GET(request: NextRequest) {
  const sessionId = request.nextUrl.searchParams.get("sessionId") || "";
  const state = await getBattleSessionState(sessionId);
  return NextResponse.json({ state, storage: getBattleStorageMode(), serverNow: new Date().toISOString() });
}

export async function POST(request: NextRequest) {
  let input: BattleSessionStateInput;
  try {
    input = (await request.json()) as BattleSessionStateInput;
  } catch {
    return NextResponse.json({ error: "invalid json body" }, { status: 400 });
  }

  try {
    const hasHumanStartedAt = Object.prototype.hasOwnProperty.call(input, "humanStartedAt");
    const state = await updateBattleSessionState({
      ...input,
      humanStartedAt: hasHumanStartedAt ? input.humanStartedAt : new Date().toISOString(),
    });
    return NextResponse.json({ state, serverNow: new Date().toISOString() });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "session update failed" },
      { status: 400 },
    );
  }
}

export async function DELETE(request: NextRequest) {
  if (requiresResetToken() && tokenFrom(request) !== process.env.BATTLE_RESET_TOKEN) {
    return NextResponse.json({ error: "invalid reset token" }, { status: 401 });
  }

  const sessionId = request.nextUrl.searchParams.get("sessionId") || "";
  const scope = request.nextUrl.searchParams.get("scope") || "session";
  if (!["session", "timer"].includes(scope)) {
    return NextResponse.json({ error: "invalid reset scope" }, { status: 400 });
  }
  try {
    const reset = scope === "timer" ? await resetBattleTimer(sessionId) : await resetBattleSession(sessionId);
    return NextResponse.json({ reset, serverNow: new Date().toISOString() });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "session reset failed" },
      { status: 400 },
    );
  }
}
