import { DEFAULT_PARTYKIT_HOST } from "./constants";

export function getPartykitHost(): string {
  const configuredHost = (import.meta.env.VITE_PARTYKIT_HOST as string | undefined)?.trim();
  return (configuredHost || DEFAULT_PARTYKIT_HOST).replace(/\/+$/, "");
}

export function getNoteRoomId(noteId: string): string {
  return noteId.trim();
}

