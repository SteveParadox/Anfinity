import { describe, expect, it } from "vitest";
import {
  getThinkingSessionIdFromRoomId,
  getThinkingSessionRoomId,
  isThinkingSessionRoomId,
} from "./room";

describe("thinking session room helpers", () => {
  it("builds and parses prefixed room ids", () => {
    const roomId = getThinkingSessionRoomId("session-123");

    expect(roomId).toBe("thinking-session:session-123");
    expect(isThinkingSessionRoomId(roomId)).toBe(true);
    expect(getThinkingSessionIdFromRoomId(roomId)).toBe("session-123");
    expect(getThinkingSessionRoomId(" thinking-session:session-123 ")).toBe("thinking-session:session-123");
  });

  it("leaves non-prefixed ids untouched", () => {
    expect(isThinkingSessionRoomId("note-123")).toBe(false);
    expect(getThinkingSessionIdFromRoomId("note-123")).toBe("note-123");
  });
});
