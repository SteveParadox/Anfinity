const THINKING_SESSION_ROOM_PREFIX = "thinking-session:";

export function getThinkingSessionRoomId(sessionId: string): string {
  const normalizedSessionId = getThinkingSessionIdFromRoomId(sessionId);
  return `${THINKING_SESSION_ROOM_PREFIX}${normalizedSessionId}`;
}

export function isThinkingSessionRoomId(roomId: string): boolean {
  return roomId.trim().startsWith(THINKING_SESSION_ROOM_PREFIX);
}

export function getThinkingSessionIdFromRoomId(roomId: string): string {
  const normalizedRoomId = roomId.trim();
  return isThinkingSessionRoomId(normalizedRoomId)
    ? normalizedRoomId.slice(THINKING_SESSION_ROOM_PREFIX.length).trim()
    : normalizedRoomId;
}
