import { describe, expect, it, vi } from "vitest";
import { DEFAULT_PARTYKIT_HOST } from "./constants";
import { getNoteIdFromRoomId, getNoteRoomId, getPartykitHost, getRealtimeConnection, isNoteRoomId } from "./noteRoom";

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


it("honors explicit local websocket protocols and rejects private production hosts", () => {
  vi.stubEnv("VITE_REALTIME_HOST", "http://dev.example:8787");
  expect(getRealtimeConnection()).toEqual({ host: "dev.example:8787", protocol: "ws" });
  vi.stubEnv("PROD", true);
  vi.stubEnv("VITE_REALTIME_HOST", "192.168.1.2:8787");
  expect(() => getRealtimeConnection()).toThrow("Production real-time");
  vi.stubEnv("VITE_REALTIME_HOST", "worker.example");
  expect(getRealtimeConnection().protocol).toBe("wss");
  vi.unstubAllEnvs();
});
