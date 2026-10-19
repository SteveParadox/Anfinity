import { DEFAULT_PARTYKIT_HOST, NOTE_ROOM_PREFIX } from "./constants";

export function getPartykitHost(): string {
  const configuredHost = (import.meta.env.VITE_REALTIME_HOST as string | undefined)?.trim()
    || (import.meta.env.VITE_PARTYKIT_HOST as string | undefined)?.trim();
  if (!configuredHost && import.meta.env.PROD) throw new Error("VITE_REALTIME_HOST is required in production");
  const raw = (configuredHost || DEFAULT_PARTYKIT_HOST).replace(/\/+$/, "");
  const url = new URL(raw.includes("://") ? raw : `https://${raw}`);
  if (!["http:", "https:", "ws:", "wss:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== "/") {
    throw new Error("Invalid real-time host; supply a hostname with an optional port");
  }
  if (import.meta.env.PROD && (/^(localhost|127\.|\[::1\])/.test(url.hostname) || ["http:", "ws:"].includes(url.protocol))) {
    throw new Error("Production real-time service requires a public HTTPS/WSS host");
  }
  return url.host;
}

export function getNoteRoomId(noteId: string): string {
  const normalizedNoteId = getNoteIdFromRoomId(noteId);
  return `${NOTE_ROOM_PREFIX}${normalizedNoteId}`;
}

export function isNoteRoomId(roomId: string): boolean {
  return roomId.trim().startsWith(NOTE_ROOM_PREFIX);
}

export function getNoteIdFromRoomId(roomId: string): string {
  const normalizedRoomId = roomId.trim();
  return isNoteRoomId(normalizedRoomId)
    ? normalizedRoomId.slice(NOTE_ROOM_PREFIX.length).trim()
    : normalizedRoomId;
}

export const getRealtimeHost = getPartykitHost;
