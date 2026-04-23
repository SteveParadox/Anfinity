import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PartySocket from "partysocket";
import type { ThinkingSession } from "@/types";
import { getPartykitHost } from "@/lib/collaboration/noteRoom";
import { transformThinkingSessionFromAPI } from "@/lib/transformers";
import { getThinkingSessionRoomId } from "@/lib/thinkingSessions/room";
import {
  parseThinkingSessionServerMessage,
  type ThinkingSessionClientMessage,
  type ThinkingSessionPresence,
  serializeThinkingSessionMessage,
} from "@/lib/thinkingSessions/protocol";

type ThinkingRoomStatus = "idle" | "connecting" | "connected" | "disconnected";

type ThinkingSessionRoomOptions = {
  sessionId?: string | null;
  token?: string | null;
  enabled?: boolean;
};

type ThinkingSessionRoomState = {
  socket: PartySocket | null;
  status: ThinkingRoomStatus;
  session: ThinkingSession | null;
  participants: ThinkingSessionPresence[];
  activeRunId: string | null;
  lastError: string | null;
  requestSnapshot: () => boolean;
  submitContribution: (content: string) => boolean;
  toggleVote: (contributionId: string) => boolean;
  transitionPhase: (targetPhase: ThinkingSession["phase"]) => boolean;
  updateRefinement: (refinedOutput: string) => boolean;
};

const HEARTBEAT_INTERVAL_MS = 30_000;

function upsertPresence(
  current: ThinkingSessionPresence[],
  participant: ThinkingSessionPresence,
): ThinkingSessionPresence[] {
  const next = current.filter((entry) => entry.userId !== participant.userId);
  next.push(participant);
  next.sort((left, right) => left.name.localeCompare(right.name));
  return next;
}

export function useThinkingSessionRoom({
  sessionId,
  token,
  enabled = true,
}: ThinkingSessionRoomOptions): ThinkingSessionRoomState {
  const [socket, setSocket] = useState<PartySocket | null>(null);
  const [status, setStatus] = useState<ThinkingRoomStatus>("idle");
  const [session, setSession] = useState<ThinkingSession | null>(null);
  const [participants, setParticipants] = useState<ThinkingSessionPresence[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  const socketRef = useRef<PartySocket | null>(null);
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearHeartbeat = useCallback(() => {
    if (heartbeatRef.current) {
      clearInterval(heartbeatRef.current);
      heartbeatRef.current = null;
    }
  }, []);

  const sendMessage = useCallback((message: ThinkingSessionClientMessage) => {
    const currentSocket = socketRef.current;
    if (!currentSocket || currentSocket.readyState !== WebSocket.OPEN) {
      setLastError("Live thinking session room is not connected");
      return false;
    }

    currentSocket.send(serializeThinkingSessionMessage(message));
    return true;
  }, []);

  const requestSnapshot = useCallback(() => {
    return sendMessage({
      type: "request_snapshot",
      payload: {},
    });
  }, [sendMessage]);

  const submitContribution = useCallback((content: string) => {
    return sendMessage({
      type: "submit_contribution",
      payload: { content },
    });
  }, [sendMessage]);

  const toggleVote = useCallback((contributionId: string) => {
    return sendMessage({
      type: "toggle_vote",
      payload: { contributionId },
    });
  }, [sendMessage]);

  const transitionPhase = useCallback((targetPhase: ThinkingSession["phase"]) => {
    return sendMessage({
      type: "transition_phase",
      payload: { targetPhase },
    });
  }, [sendMessage]);

  const updateRefinement = useCallback((refinedOutput: string) => {
    return sendMessage({
      type: "update_refinement",
      payload: { refinedOutput },
    });
  }, [sendMessage]);

  useEffect(() => {
    clearHeartbeat();

    const trimmedSessionId = sessionId?.trim();
    const trimmedToken = token?.trim();

    if (!enabled || !trimmedSessionId || !trimmedToken) {
      socketRef.current?.close();
      socketRef.current = null;
      setSocket(null);
      setStatus("idle");
      setSession(null);
      setParticipants([]);
      setActiveRunId(null);
      setLastError(null);
      return;
    }

    const nextSocket = new PartySocket({
      host: getPartykitHost(),
      room: getThinkingSessionRoomId(trimmedSessionId),
      query: {
        token: trimmedToken,
      },
    });

    let disposed = false;

    const handleOpen = () => {
      if (disposed) {
        return;
      }

      setStatus("connected");
      setLastError(null);
      nextSocket.send(serializeThinkingSessionMessage({
        type: "request_snapshot",
        payload: {},
      }));

      clearHeartbeat();
      heartbeatRef.current = setInterval(() => {
        if (nextSocket.readyState !== WebSocket.OPEN) {
          return;
        }

        nextSocket.send(serializeThinkingSessionMessage({
          type: "heartbeat",
          payload: {},
        }));
      }, HEARTBEAT_INTERVAL_MS);
    };

    const handleMessage = (event: MessageEvent) => {
      if (disposed || typeof event.data !== "string") {
        return;
      }

      const message = parseThinkingSessionServerMessage(event.data);
      if (!message) {
        return;
      }

      if (message.type === "presence_snapshot") {
        setParticipants(message.payload.participants);
        return;
      }

      if (message.type === "presence_join") {
        setParticipants((current) => upsertPresence(current, message.payload.participant));
        return;
      }

      if (message.type === "presence_leave") {
        setParticipants((current) => current.filter((entry) => entry.userId !== message.payload.userId));
        return;
      }

      if (message.type === "session_snapshot" || message.type === "session_updated") {
        const nextSession = transformThinkingSessionFromAPI(message.payload.session);
        setSession(nextSession);
        setActiveRunId(nextSession.activeSynthesisRunId ?? null);
        return;
      }

      if (message.type === "synthesis_started") {
        setActiveRunId(message.payload.runId);
        return;
      }

      if (message.type === "synthesis_chunk") {
        setActiveRunId(message.payload.runId);
        setSession((current) => {
          if (!current) {
            return current;
          }

          const activeSynthesisRun = current.activeSynthesisRun
            && current.activeSynthesisRun.id === message.payload.runId
            ? {
                ...current.activeSynthesisRun,
                outputText: message.payload.fullText,
              }
            : current.activeSynthesisRun;

          return {
            ...current,
            synthesisOutput: message.payload.fullText,
            activeSynthesisRun,
            synthesisRuns: current.synthesisRuns.map((run) => (
              run.id === message.payload.runId
                ? { ...run, outputText: message.payload.fullText }
                : run
            )),
          };
        });
        return;
      }

      if (message.type === "synthesis_completed") {
        setSession(transformThinkingSessionFromAPI(message.payload.session));
        setActiveRunId(null);
        return;
      }

      if (message.type === "synthesis_failed") {
        setSession(transformThinkingSessionFromAPI(message.payload.session));
        setActiveRunId(null);
        setLastError(message.payload.message);
        return;
      }

      if (message.type === "server_error") {
        setLastError(message.payload.message);
      }
    };

    const handleClose = () => {
      if (disposed) {
        return;
      }

      clearHeartbeat();
      setStatus("disconnected");
      setParticipants([]);
    };

    const handleError = () => {
      if (disposed) {
        return;
      }

      setLastError("Live thinking session connection failed");
    };

    nextSocket.addEventListener("open", handleOpen);
    nextSocket.addEventListener("message", handleMessage);
    nextSocket.addEventListener("close", handleClose);
    nextSocket.addEventListener("error", handleError);

    socketRef.current = nextSocket;
    setSocket(nextSocket);
    setStatus("connecting");
    setParticipants([]);
    setActiveRunId(null);
    setLastError(null);

    return () => {
      disposed = true;
      clearHeartbeat();
      nextSocket.removeEventListener("open", handleOpen);
      nextSocket.removeEventListener("message", handleMessage);
      nextSocket.removeEventListener("close", handleClose);
      nextSocket.removeEventListener("error", handleError);
      nextSocket.close();

      if (socketRef.current === nextSocket) {
        socketRef.current = null;
      }
    };
  }, [clearHeartbeat, enabled, sessionId, token]);

  return useMemo(() => ({
    socket,
    status,
    session,
    participants,
    activeRunId,
    lastError,
    requestSnapshot,
    submitContribution,
    toggleVote,
    transitionPhase,
    updateRefinement,
  }), [
    activeRunId,
    lastError,
    participants,
    requestSnapshot,
    session,
    socket,
    status,
    submitContribution,
    toggleVote,
    transitionPhase,
    updateRefinement,
  ]);
}
