"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { useEffect, useState } from "react";

export type BattleRealtimeStatus = "disabled" | "connecting" | "subscribed" | "fallback";

type BattleRealtimeOptions = {
  sessionId: string;
  onRecordsChange: () => void | Promise<void>;
  onSessionChange: () => void | Promise<void>;
};

const realtimeUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const realtimeAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";
const recordTable = process.env.NEXT_PUBLIC_SUPABASE_BATTLE_TABLE || "battle_records";
const stateTable = process.env.NEXT_PUBLIC_SUPABASE_BATTLE_STATE_TABLE || "battle_session_states";

let realtimeClient: SupabaseClient | null = null;

function hasRealtimeConfig() {
  return Boolean(realtimeUrl && realtimeAnonKey);
}

function getRealtimeClient() {
  if (!hasRealtimeConfig()) return null;
  realtimeClient ||= createClient(realtimeUrl, realtimeAnonKey, {
    auth: { persistSession: false },
    realtime: { params: { eventsPerSecond: 10 } },
  });
  return realtimeClient;
}

function channelName(sessionId: string) {
  const safeSessionId = sessionId.replace(/[^a-z0-9_-]+/gi, "-") || "battle-live";
  return `battle-${safeSessionId}-${Math.random().toString(36).slice(2)}`;
}

export function useBattleRealtime({ sessionId, onRecordsChange, onSessionChange }: BattleRealtimeOptions) {
  const [status, setStatus] = useState<BattleRealtimeStatus>(() => (hasRealtimeConfig() ? "connecting" : "disabled"));

  useEffect(() => {
    const supabase = getRealtimeClient();
    if (!supabase || !sessionId) {
      const timer = window.setTimeout(() => setStatus("disabled"), 0);
      return () => window.clearTimeout(timer);
    }

    let active = true;
    const connectingTimer = window.setTimeout(() => setStatus("connecting"), 0);

    const channel = supabase
      .channel(channelName(sessionId))
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: recordTable, filter: `session_id=eq.${sessionId}` },
        () => void onRecordsChange(),
      )
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: stateTable, filter: `session_id=eq.${sessionId}` },
        () => void onSessionChange(),
      )
      .subscribe((nextStatus) => {
        if (!active) return;
        if (nextStatus === "SUBSCRIBED") {
          setStatus("subscribed");
          return;
        }
        if (nextStatus === "CHANNEL_ERROR" || nextStatus === "TIMED_OUT" || nextStatus === "CLOSED") {
          setStatus("fallback");
        }
      });

    return () => {
      active = false;
      window.clearTimeout(connectingTimer);
      void supabase.removeChannel(channel);
    };
  }, [onRecordsChange, onSessionChange, sessionId]);

  return status;
}
