export type { Connection, ConnectionContext } from "partyserver";
export type Request = globalThis.Request;
export type Lobby = { env: import("./security").RealtimeEnv };
export type Room = { id: string; env: Record<string, unknown>; broadcast: (...args: unknown[]) => void; storage: unknown };
