import { describe, expect, it, vi } from "vitest";
import { DEFAULT_PARTYKIT_HOST } from "./constants";
import { getNoteIdFromRoomId, getNoteRoomId, getPartykitHost, isNoteRoomId } from "./noteRoom";

describe("note room helpers", () => {
  it("trims note room identifiers", () => {
    expect(getNoteRoomId("  note-123  ")).toBe("note:note-123");
  });

  it("parses prefixed and legacy note room identifiers", () => {
    expect(isNoteRoomId("note:note-123")).toBe(true);
    expect(getNoteIdFromRoomId("note:note-123")).toBe("note-123");
    expect(getNoteIdFromRoomId("note-123")).toBe("note-123");
    expect(getNoteRoomId("note:note-123")).toBe("note:note-123");
  });

  it("uses the configured PartyKit host when provided", () => {
    vi.stubEnv("VITE_PARTYKIT_HOST", "collab.example.com///");
    expect(getPartykitHost()).toBe("collab.example.com");
    vi.unstubAllEnvs();
  });

  it("falls back to the default PartyKit host", () => {
    vi.unstubAllEnvs();
    expect(getPartykitHost()).toBe(DEFAULT_PARTYKIT_HOST);
  });
});
