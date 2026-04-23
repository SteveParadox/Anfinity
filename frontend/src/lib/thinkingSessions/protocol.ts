import type {
  ThinkingSession,
  ThinkingSessionPhase,
} from "@/types";

export type ThinkingSessionPresence = {
  userId: string;
  email: string;
  name: string;
  color: string;
  canParticipate: boolean;
  canControl: boolean;
  isHost: boolean;
  connectedAt: string;
  connectionCount: number;
};

export type ThinkingSessionClientMessage =
  | {
      type: "request_snapshot";
      payload: Record<string, never>;
    }
  | {
      type: "heartbeat";
      payload: Record<string, never>;
    }
  | {
      type: "submit_contribution";
      payload: {
        content: string;
      };
    }
  | {
      type: "toggle_vote";
      payload: {
        contributionId: string;
      };
    }
  | {
      type: "transition_phase";
      payload: {
        targetPhase: ThinkingSessionPhase;
      };
    }
  | {
      type: "update_refinement";
      payload: {
        refinedOutput: string;
      };
    };

export type ThinkingSessionServerMessage =
  | {
      type: "presence_snapshot";
      payload: {
        participants: ThinkingSessionPresence[];
      };
    }
  | {
      type: "presence_join";
      payload: {
        participant: ThinkingSessionPresence;
      };
    }
  | {
      type: "presence_leave";
      payload: {
        userId: string;
      };
    }
  | {
      type: "session_snapshot";
      payload: {
        session: ThinkingSession;
      };
    }
  | {
      type: "session_updated";
      payload: {
        session: ThinkingSession;
      };
    }
  | {
      type: "synthesis_started";
      payload: {
        sessionId: string;
        runId: string;
        model?: string;
      };
    }
  | {
      type: "synthesis_chunk";
      payload: {
        sessionId: string;
        runId: string;
        text: string;
        fullText: string;
      };
    }
  | {
      type: "synthesis_completed";
      payload: {
        session: ThinkingSession;
        runId: string;
        text: string;
      };
    }
  | {
      type: "synthesis_failed";
      payload: {
        session: ThinkingSession;
        runId: string;
        message: string;
      };
    }
  | {
      type: "server_error";
      payload: {
        code: string;
        message: string;
      };
    };

const THINKING_SESSION_PHASES = new Set<ThinkingSessionPhase>([
  "waiting",
  "gathering",
  "synthesizing",
  "refining",
  "completed",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isBoolean(value: unknown): value is boolean {
  return typeof value === "boolean";
}

function isThinkingSessionPhase(value: unknown): value is ThinkingSessionPhase {
  return isString(value) && THINKING_SESSION_PHASES.has(value as ThinkingSessionPhase);
}

function isPositiveConnectionCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function isThinkingSessionPresence(value: unknown): value is ThinkingSessionPresence {
  return isRecord(value)
    && isString(value.userId)
    && isString(value.email)
    && isString(value.name)
    && isString(value.color)
    && isBoolean(value.canParticipate)
    && isBoolean(value.canControl)
    && isBoolean(value.isHost)
    && isString(value.connectedAt)
    && isPositiveConnectionCount(value.connectionCount);
}

function isThinkingSessionLike(value: unknown): value is ThinkingSession {
  return isRecord(value)
    && isString(value.id)
    && (value.phase === undefined || isThinkingSessionPhase(value.phase));
}

export function isThinkingSessionServerMessage(
  value: unknown,
): value is ThinkingSessionServerMessage {
  if (!isRecord(value) || !isString(value.type) || !isRecord(value.payload)) {
    return false;
  }

  if (value.type === "presence_snapshot") {
    return Array.isArray(value.payload.participants)
      && value.payload.participants.every(isThinkingSessionPresence);
  }

  if (value.type === "presence_join") {
    return isThinkingSessionPresence(value.payload.participant);
  }

  if (value.type === "presence_leave") {
    return isString(value.payload.userId);
  }

  if (value.type === "session_snapshot" || value.type === "session_updated") {
    return isThinkingSessionLike(value.payload.session);
  }

  if (value.type === "synthesis_started") {
    return isString(value.payload.sessionId)
      && isString(value.payload.runId)
      && (value.payload.model === undefined || isString(value.payload.model));
  }

  if (value.type === "synthesis_chunk") {
    return isString(value.payload.sessionId)
      && isString(value.payload.runId)
      && isString(value.payload.text)
      && isString(value.payload.fullText);
  }

  if (value.type === "synthesis_completed") {
    return isThinkingSessionLike(value.payload.session)
      && isString(value.payload.runId)
      && isString(value.payload.text);
  }

  if (value.type === "synthesis_failed") {
    return isThinkingSessionLike(value.payload.session)
      && isString(value.payload.runId)
      && isString(value.payload.message);
  }

  if (value.type === "server_error") {
    return isString(value.payload.code) && isString(value.payload.message);
  }

  return false;
}

export function parseThinkingSessionServerMessage(
  rawMessage: string,
): ThinkingSessionServerMessage | null {
  try {
    const parsed = JSON.parse(rawMessage) as unknown;
    return isThinkingSessionServerMessage(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function serializeThinkingSessionMessage(
  message: ThinkingSessionClientMessage | ThinkingSessionServerMessage,
): string {
  return JSON.stringify(message);
}
