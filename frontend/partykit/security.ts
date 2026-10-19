import type { Connection, WSMessage, Server } from "partyserver";

export interface RealtimeEnv extends Record<string, unknown> {
  NOTES: DurableObjectNamespace<Server<RealtimeEnv>>;
  THINKING: DurableObjectNamespace<Server<RealtimeEnv>>;
  REALTIME_BACKEND_URL?: string;
  PARTYKIT_BACKEND_URL?: string;
  REALTIME_ALLOWED_ORIGINS?: string;
  REALTIME_ENVIRONMENT?: string;
  REALTIME_SERVICE_SECRET?: string;
}

export function parseRoomPath(path: string): { room: string; kind: "note" | "thinking-session" } | null {
  const match = /^\/parties\/main\/([^/]+)$/.exec(path);
  if (!match) return null;
  let room: string;
  try { room = decodeURIComponent(match[1]); } catch { return null; }
  const parsed = /^(note|thinking-session):([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/i.exec(room);
  return parsed ? { room: `${parsed[1].toLowerCase()}:${parsed[2].toLowerCase()}`, kind: parsed[1].toLowerCase() as "note" | "thinking-session" } : null;
}

export function backendUrl(env: RealtimeEnv): string {
  const value = env.REALTIME_BACKEND_URL ?? env.PARTYKIT_BACKEND_URL;
  if (!value) throw new Error("REALTIME_BACKEND_URL is required");
  const url = new URL(value);
  if (env.REALTIME_ENVIRONMENT !== "development" && url.protocol !== "https:") throw new Error("Backend HTTPS required");
  return value.replace(/\/+$/, "");
}

export async function authorizeMessage(env: RealtimeEnv, token: string, room: string): Promise<{ can_view: boolean; can_update?: boolean } | null> {
  const [kind, id] = room.split(":");
  try {
    const response = await fetch(`${backendUrl(env)}/${kind === "note" ? "notes" : "thinking-sessions"}/${id}/access`, {
      headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) return null;
    const access = await response.json() as { can_view: boolean; can_update?: boolean };
    return access.can_view ? access : null;
  } catch { return null; }
}

const budgets = new WeakMap<object, { start: number; count: number }>();
export function checkMessageBudget(connection: Connection, message: WSMessage): boolean {
  const bytes = typeof message === "string" ? new TextEncoder().encode(message).length : message.byteLength;
  if (bytes > 1024 * 1024) { connection.close(1009, "Message too large"); return false; }
  const now = Date.now();
  let budget = budgets.get(connection);
  if (!budget || now - budget.start > 1000) { budget = { start: now, count: 0 }; budgets.set(connection, budget); }
  if (++budget.count > 120) { connection.close(4429, "Message rate exceeded"); return false; }
  return true;
}

export function watchAuthorization(env: RealtimeEnv, connection: Connection, token: string, room: string): () => void {
  let checking = false;
  const timer = setInterval(async () => {
    if (checking) return;
    checking = true;
    try {
      if (!await authorizeMessage(env, token, room)) connection.close(4403, "Access expired or revoked");
    } finally { checking = false; }
  }, 30_000);
  return () => clearInterval(timer);
}
