import { NextRequest, NextResponse } from "next/server";

import { getBattleSessionState, getBattleStorageMode, updateBattleSessionState } from "@/lib/store";
import { BattleSessionStateInput } from "@/lib/types";

export const dynamic = "force-dynamic";

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
    const state = await updateBattleSessionState({
      ...input,
      humanStartedAt: new Date().toISOString(),
    });
    return NextResponse.json({ state, serverNow: new Date().toISOString() });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "session update failed" },
      { status: 400 },
    );
  }
}
