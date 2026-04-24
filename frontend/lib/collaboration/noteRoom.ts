import { DEFAULT_PARTYKIT_HOST, NOTE_ROOM_PREFIX } from "./constants";

export function getPartykitHost(): string {
  const configuredHost = (import.meta.env.VITE_PARTYKIT_HOST as string | undefined)?.trim();
  return (configuredHost || DEFAULT_PARTYKIT_HOST).replace(/\/+$/, "");
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
