import { describe, expect, it } from "vitest";
import {
  isThinkingSessionServerMessage,
  parseThinkingSessionServerMessage,
  type ThinkingSessionServerMessage,
} from "./protocol";

const participant = {
  userId: "user-1",
  email: "user@example.com",
  name: "User One",
  color: "#33aaee",
  canParticipate: true,
  canControl: false,
  isHost: false,
  connectedAt: new Date("2026-04-23T12:00:00.000Z").toISOString(),
  connectionCount: 1,
};

const session = {
  id: "session-1",
  workspaceId: "workspace-1",
  noteId: null,
  title: "Session One",
  promptContext: null,
  createdByUserId: "user-1",
  hostUserId: "user-1",
  phase: "gathering" as const,
  roomId: "thinking-session:session-1",
  activeSynthesisRunId: null,
  synthesisOutput: "",
  refinedOutput: "",
  finalOutput: "",
  lastRefinedByUserId: null,
  participants: [],
  contributions: [],
  synthesisRuns: [],
  activeSynthesisRun: null,
};

describe("thinking session protocol validation", () => {
  it("accepts well-formed presence and session messages", () => {
    const messages: ThinkingSessionServerMessage[] = [
      {
        type: "presence_snapshot",
        payload: {
          participants: [participant],
        },
      },
      {
        type: "presence_join",
        payload: {
          participant,
        },
      },
      {
        type: "presence_leave",
        payload: {
          userId: participant.userId,
        },
      },
      {
        type: "session_snapshot",
        payload: {
          session,
        },
      },
      {
        type: "session_updated",
        payload: {
          session,
        },
      },
      {
        type: "synthesis_started",
        payload: {
          sessionId: session.id,
          runId: "run-1",
          model: "gpt-5.4",
        },
      },
      {
        type: "synthesis_chunk",
        payload: {
          sessionId: session.id,
          runId: "run-1",
          text: "hello",
          fullText: "hello world",
        },
      },
      {
        type: "synthesis_completed",
        payload: {
          session,
          runId: "run-1",
          text: "done",
        },
      },
      {
        type: "synthesis_failed",
        payload: {
          session,
          runId: "run-1",
          message: "failed",
        },
      },
      {
        type: "server_error",
        payload: {
          code: "invalid_message",
          message: "Malformed thinking session payload",
        },
      },
    ];

    for (const message of messages) {
      expect(isThinkingSessionServerMessage(message)).toBe(true);
      expect(parseThinkingSessionServerMessage(JSON.stringify(message))).toEqual(message);
    }
  });

  it("rejects malformed JSON and unknown message types", () => {
    expect(parseThinkingSessionServerMessage("{")).toBeNull();
    expect(parseThinkingSessionServerMessage(JSON.stringify({
      type: "phase_magic",
      payload: {},
    }))).toBeNull();
  });

  it("rejects invalid presence and session payloads", () => {
    expect(isThinkingSessionServerMessage({
      type: "presence_join",
      payload: {
        participant: {
          ...participant,
          connectionCount: 0,
        },
      },
    })).toBe(false);

    expect(isThinkingSessionServerMessage({
      type: "session_snapshot",
      payload: {
        session: {
          id: "session-1",
          phase: "not-a-phase",
        },
      },
    })).toBe(false);
  });

  it("rejects incomplete synthesis payloads", () => {
    expect(isThinkingSessionServerMessage({
      type: "synthesis_chunk",
      payload: {
        sessionId: "session-1",
        runId: "run-1",
        text: "hello",
      },
    })).toBe(false);
  });
});
