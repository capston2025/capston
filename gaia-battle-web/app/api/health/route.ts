import { NextResponse } from "next/server";

import { getBattleStorageMode } from "@/lib/store";

export const dynamic = "force-dynamic";

export async function GET() {
  const storage = getBattleStorageMode();
  return NextResponse.json({
    ok: storage === "supabase" || process.env.NODE_ENV !== "production",
    storage,
    supabaseConfigured: storage === "supabase",
    uploadTokenConfigured: Boolean(process.env.BATTLE_UPLOAD_TOKEN),
    defaultSessionId: process.env.NEXT_PUBLIC_DEFAULT_SESSION_ID || "",
  });
}
