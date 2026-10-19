import { Server, type Connection, type ConnectionContext, type WSMessage } from "partyserver";
import type { RealtimeEnv } from "./security";
import { authorizeMessage, checkMessageBudget, watchAuthorization } from "./security";
import {
  COLLABORATION_AUTH_TIMEOUT_MS,
  DEFAULT_PARTYKIT_BACKEND_URL,
} from "../src/lib/collaboration/constants";
import { getCollaboratorColor } from "../src/lib/collaboration/colors";
import type {
  ThinkingSession,
  ThinkingSessionPhase,
} from "../src/types";
import {
  getThinkingSessionIdFromRoomId,
} from "../src/lib/thinkingSessions/room";
import {
  type ThinkingSessionClientMessage,
  type ThinkingSessionPresence,
  type ThinkingSessionServerMessage,
  serializeThinkingSessionMessage,
} from "../src/lib/thinkingSessions/protocol";
import {
  transformThinkingSessionFromAPI,
} from "../src/lib/transformers";

const HEADER_USER_ID = "x-thinking-user-id";
const HEADER_USER_EMAIL = "x-thinking-user-email";
const HEADER_USER_NAME = "x-thinking-user-name";
const HEADER_USER_COLOR = "x-thinking-user-color";
const HEADER_CAN_PARTICIPATE = "x-thinking-can-participate";
const HEADER_CAN_CONTROL = "x-thinking-can-control";
const HEADER_IS_HOST = "x-thinking-is-host";
const HEADER_CONNECTED_AT = "x-thinking-connected-at";
const HEADER_AUTH_TOKEN = "x-thinking-auth-token";

const MAX_THINKING_MESSAGE_BYTES = 32_768;
const MAX_CONTRIBUTION_LENGTH = 5_000;
const MAX_REFINEMENT_LENGTH = 50_000;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ALLOWED_PHASES = new Set<ThinkingSessionPhase>([
  "waiting",
  "gathering",
  "synthesizing",
  "refining",
  "completed",
]);
const MAX_SYNTHESIS_STREAM_RETRIES = 3;
const SYNTHESIS_STREAM_RETRY_DELAY_MS = 2_000;

type BackendSessionAccessResponse = {
  session_id: string;
  workspace_id: string;
  room_id: string;
  can_view: boolean;
  can_participate: boolean;
  can_control: boolean;
  is_host: boolean;
  phase: ThinkingSessionPhase;
};

type BackendUserResponse = {
  id: string;
  email: string;
  full_name?: string | null;
};

type BackendThinkingTransitionResponse = {
  session: unknown;
  synthesis_run_id?: string | null;
};

type ConnectionState = {
  connectionId: string;
  userId: string;
  email: string;
  name: string;
  color: string;
  canParticipate: boolean;
  canControl: boolean;
  isHost: boolean;
  connectedAt: string;
  token: string;
};

type ActiveSynthesisStream = {
  runId: string;
  token: string;
  fullText: string;
  persistTimer: ReturnType<typeof setTimeout> | null;
  persistInFlight: Promise<void> | null;
  pendingPersistText: string;
  retryCount: number;
};

function getBackendUrl(env: Record<string, unknown>): string {
  const value = String(
    env.REALTIME_BACKEND_URL ?? env.PARTYKIT_BACKEND_URL ?? DEFAULT_PARTYKIT_BACKEND_URL,
  ).trim();

  return value.replace(/\/+$/, "");
}

function getBearerToken(requestUrl: string): string {
  const url = new URL(requestUrl);
  return url.searchParams.get("token")?.trim() ?? "";
}

function getRoomIdFromRequest(requestUrl: string): string | null {
  try {
    const url = new URL(requestUrl);
    const roomId = url.pathname.split("/").filter(Boolean).at(-1) ?? "";
    return roomId || null;
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isValidEntityId(value: string): boolean {
  return UUID_PATTERN.test(value);
}

function parseClientMessage(rawMessage: string): ThinkingSessionClientMessage | null {
  if (rawMessage.length > MAX_THINKING_MESSAGE_BYTES) {
    return null;
  }

  try {
    const parsed = JSON.parse(rawMessage) as unknown;
    if (!isRecord(parsed) || typeof parsed.type !== "string" || !isRecord(parsed.payload)) {
      return null;
    }

    if (parsed.type === "request_snapshot" || parsed.type === "heartbeat") {
      return { type: parsed.type, payload: {} };
    }

    if (parsed.type === "submit_contribution") {
      if (
        !isString(parsed.payload.content)
        || parsed.payload.content.trim().length === 0
        || parsed.payload.content.length > MAX_CONTRIBUTION_LENGTH
      ) {
        return null;
      }
      return {
        type: "submit_contribution",
        payload: {
          content: parsed.payload.content.trim(),
        },
      };
    }

    if (parsed.type === "toggle_vote") {
      if (!isString(parsed.payload.contributionId) || !isValidEntityId(parsed.payload.contributionId)) {
        return null;
      }
      return {
        type: "toggle_vote",
        payload: {
          contributionId: parsed.payload.contributionId,
        },
      };
    }

    if (parsed.type === "transition_phase") {
      if (
        !isString(parsed.payload.targetPhase)
        || !ALLOWED_PHASES.has(parsed.payload.targetPhase as ThinkingSessionPhase)
      ) {
        return null;
      }
      return {
        type: "transition_phase",
        payload: {
          targetPhase: parsed.payload.targetPhase as ThinkingSessionPhase,
        },
      };
    }

    if (parsed.type === "update_refinement") {
      if (
        !isString(parsed.payload.refinedOutput)
        || parsed.payload.refinedOutput.length > MAX_REFINEMENT_LENGTH
      ) {
        return null;
      }
      return {
        type: "update_refinement",
        payload: {
          refinedOutput: parsed.payload.refinedOutput,
        },
      };
    }
  } catch {
    return null;
  }

  return null;
}

function parseSseEvents(buffer: string): { events: unknown[]; remainder: string } {
  const events: unknown[] = [];
  const rawEvents = buffer.split("\n\n");
  const remainder = rawEvents.pop() ?? "";

  for (const rawEvent of rawEvents) {
    const data = rawEvent
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n")
      .trim();

    if (!data || data === "[DONE]") {
      continue;
    }

    try {
      events.push(JSON.parse(data) as unknown);
    } catch {
      continue;
    }
  }

  return { events, remainder };
}

async function fetchJson<T>(
  input: string,
  init: RequestInit,
  timeoutMs = COLLABORATION_AUTH_TIMEOUT_MS,
): Promise<T> {
  const maxRetries = (init.method && init.method !== "GET") ? 0 : 2;
  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetch(input, {
        ...init,
        signal: controller.signal,
      });

      clearTimeout(timeout);

      if (!response.ok) {
        throw new Error(`Backend request failed: ${response.status}`);
      }

      return (await response.json()) as T;
    } catch (error) {
      clearTimeout(timeout);
      lastError = error instanceof Error ? error : new Error(String(error));

      if (error instanceof Error && /failed: 4\d\d/.test(error.message)) {
        throw error;
      }

      if (attempt < maxRetries) {
        await new Promise((resolve) => setTimeout(resolve, Math.pow(2, attempt) * 100));
        continue;
      }

      break;
    }
  }

  throw lastError || new Error("Backend request failed after retries");
}

async function resolveConnectionContext(
  backendUrl: string,
  token: string,
  sessionId: string,
): Promise<ConnectionState> {
  const headers = {
    Authorization: `Bearer ${token}`,
  };

  const [access, currentUser] = await Promise.all([
    fetchJson<BackendSessionAccessResponse>(
      `${backendUrl}/thinking-sessions/${sessionId}/access`,
      { headers },
    ),
    fetchJson<BackendUserResponse>(`${backendUrl}/auth/me`, { headers }),
  ]);

  if (!access.can_view) {
    throw new Error("Missing thinking session view permission");
  }

  return {
    connectionId: "",
    userId: currentUser.id,
    email: currentUser.email,
    name: currentUser.full_name?.trim() || currentUser.email,
    color: getCollaboratorColor(currentUser.id),
    canParticipate: Boolean(access.can_participate),
    canControl: Boolean(access.can_control),
    isHost: Boolean(access.is_host),
    connectedAt: new Date().toISOString(),
    token,
  };
}

export class ThinkingSessionServer extends Server<RealtimeEnv> {
  private readonly connections = new Map<string, ConnectionState>();
  private readonly userConnections = new Map<string, Set<string>>();
  private readonly authWatchers = new Map<string, () => void>();
  private messageChain: Promise<void> = Promise.resolve();
  private pendingMessages = 0;
  private sessionSnapshot: ThinkingSession | null = null;
  private sessionFetchPromise: Promise<ThinkingSession> | null = null;
  private activeStream: ActiveSynthesisStream | null = null;
  private synthesisRetryTimer: ReturnType<typeof setTimeout> | null = null;


  static options = {
    hibernate: false,
  };

  static async onBeforeConnect(
    request: Request,
    lobby: { env: RealtimeEnv },
  ): Promise<Request | Response> {
    const token = getBearerToken(request.url);
    const roomId = getRoomIdFromRequest(request.url);

    if (!token) {
      return new Response("Unauthorized: Missing token", { status: 401 });
    }

    if (!roomId) {
      return new Response("Unauthorized: Missing room id", { status: 401 });
    }

    const sessionId = getThinkingSessionIdFromRoomId(roomId);

    if (!sessionId || !isValidEntityId(sessionId)) {
      return new Response("Unauthorized: Invalid thinking session room id", { status: 401 });
    }

    try {
      const presence = await resolveConnectionContext(
        getBackendUrl(lobby.env),
        token,
        sessionId,
      );

      request.headers.set(HEADER_USER_ID, presence.userId);
      request.headers.set(HEADER_USER_EMAIL, presence.email);
      request.headers.set(HEADER_USER_NAME, presence.name);
      request.headers.set(HEADER_USER_COLOR, presence.color);
      request.headers.set(HEADER_CAN_PARTICIPATE, presence.canParticipate ? "1" : "0");
      request.headers.set(HEADER_CAN_CONTROL, presence.canControl ? "1" : "0");
      request.headers.set(HEADER_IS_HOST, presence.isHost ? "1" : "0");
      request.headers.set(HEADER_CONNECTED_AT, presence.connectedAt);
      request.headers.set(HEADER_AUTH_TOKEN, token);

      return request;
    } catch {
      console.warn(JSON.stringify({ event: "thinking_auth_failed" }));
      return new Response("Unauthorized", { status: 401 });
    }
  }

  async onConnect(
    connection: Connection,
    ctx: ConnectionContext,
  ): Promise<void> {
    const state = this.readConnectionState(connection, ctx.request.headers);
    const isFirstConnection = this.registerConnection(connection, state);
    this.authWatchers.set(connection.id, watchAuthorization(this.env, connection, state.token, this.name));

    // The ping response is a fresh authoritative snapshot, including on reconnect.
    await this.pingParticipant(state.token);
    await this.ensureSessionSnapshot(state.token);

    this.sendToConnection(connection, {
      type: "presence_snapshot",
      payload: {
        participants: this.getPresenceSnapshot(),
      },
    });

    if (this.sessionSnapshot) {
      this.sendToConnection(connection, {
        type: "session_snapshot",
        payload: {
          session: this.getHydratedSessionSnapshot(),
        },
      });
    }

    this.ensurePendingSynthesisStream(state.token);

    if (isFirstConnection) {
      this.broadcastEvent(
        {
          type: "presence_join",
          payload: {
            participant: this.toPresence(state.userId),
          },
        },
        [connection.id],
      );
    }
  }

  onMessage(sender: Connection, message: WSMessage): Promise<void> {
    if (!checkMessageBudget(sender, message)) return Promise.resolve();
    if (this.pendingMessages >= 256) { sender.close(4429, "Room queue full"); return Promise.resolve(); }
    this.pendingMessages++;
    const pending = this.messageChain.then(() => this.processMessage(sender, message)).finally(() => { this.pendingMessages--; });
    this.messageChain = pending.catch(() => undefined);
    return pending;
  }

  private async processMessage(
    sender: Connection,
    message: WSMessage,
  ): Promise<void> {
    if (typeof message !== "string") {
      return;
    }

    const state = this.connections.get(sender.id);
    if (!state) {
      return;
    }

    if (!await authorizeMessage(this.env, state.token, `thinking-session:${this.sessionId}`)) {
      sender.close(4403, "Access expired or revoked");
      return;
    }
    const parsed = parseClientMessage(message);
    if (!parsed) {
      this.sendError(sender, "invalid_message", "Malformed thinking session payload");
      return;
    }

    try {
      if (parsed.type === "request_snapshot") {
        const snapshot = await this.fetchSessionSnapshot(state.token);
        this.sessionSnapshot = snapshot;
        this.sendToConnection(sender, {
          type: "session_snapshot",
          payload: {
            session: snapshot,
          },
        });
        this.ensurePendingSynthesisStream(state.token);
        return;
      }

      if (parsed.type === "heartbeat") {
        await this.pingParticipant(state.token);
        return;
      }

      if (parsed.type === "submit_contribution") {
        if (!state.canParticipate) {
          this.sendError(sender, "forbidden", "You do not have permission to participate");
          return;
        }
        const session = await this.createContribution(state.token, parsed.payload.content);
        this.broadcastSessionUpdate(session);
        return;
      }

      if (parsed.type === "toggle_vote") {
        if (!state.canParticipate) {
          this.sendError(sender, "forbidden", "You do not have permission to vote");
          return;
        }
        const session = await this.toggleVote(state.token, parsed.payload.contributionId);
        this.broadcastSessionUpdate(session);
        return;
      }

      if (parsed.type === "update_refinement") {
        if (!state.canControl) {
          this.sendError(sender, "forbidden", "You do not have permission to refine this session");
          return;
        }
        const session = await this.updateRefinement(state.token, parsed.payload.refinedOutput);
        this.broadcastSessionUpdate(session);
        return;
      }

      if (parsed.type === "transition_phase") {
        if (!state.canControl) {
          this.sendError(sender, "forbidden", "You do not have permission to control this session");
          return;
        }
        const result = await this.transitionPhase(state.token, parsed.payload.targetPhase);
        this.broadcastSessionUpdate(transformThinkingSessionFromAPI(result.session));
        if (result.synthesis_run_id) {
          this.startSynthesisStream(result.synthesis_run_id, state.token);
        }
      }
    } catch {
      console.warn(JSON.stringify({ event: "thinking_request_failed" }));
      this.sendError(sender, "request_failed", "Session request failed; refresh the snapshot before retrying");
    }
  }

  async onClose(connection: Connection): Promise<void> {
    this.unregisterConnection(connection);
  }

  async onError(connection: Connection): Promise<void> {
    this.unregisterConnection(connection);
  }

  private get backendUrl(): string {
    return getBackendUrl(this.env);
  }

  private get sessionId(): string {
    return getThinkingSessionIdFromRoomId(this.name);
  }

  private readConnectionState(
    connection: Connection,
    headers: Pick<Headers, "get">,
  ): ConnectionState {
    return {
      connectionId: connection.id,
      userId: headers.get(HEADER_USER_ID)?.trim() || connection.id,
      email: headers.get(HEADER_USER_EMAIL)?.trim() || "",
      name: headers.get(HEADER_USER_NAME)?.trim() || "Collaborator",
      color: headers.get(HEADER_USER_COLOR)?.trim() || getCollaboratorColor(connection.id),
      canParticipate: headers.get(HEADER_CAN_PARTICIPATE) === "1",
      canControl: headers.get(HEADER_CAN_CONTROL) === "1",
      isHost: headers.get(HEADER_IS_HOST) === "1",
      connectedAt: headers.get(HEADER_CONNECTED_AT)?.trim() || new Date().toISOString(),
      token: headers.get(HEADER_AUTH_TOKEN)?.trim() || "",
    };
  }

  private registerConnection(connection: Connection, state: ConnectionState): boolean {
    connection.setState(state);
    this.connections.set(connection.id, state);

    const existingConnections = this.userConnections.get(state.userId);
    const isFirstConnection = !existingConnections || existingConnections.size === 0;
    const nextConnections = existingConnections ?? new Set<string>();
    nextConnections.add(connection.id);
    this.userConnections.set(state.userId, nextConnections);
    return isFirstConnection;
  }

  private unregisterConnection(connection: Connection): void {
    const state = this.connections.get(connection.id);
    if (!state) {
      return;
    }

    this.connections.delete(connection.id);
    this.authWatchers.get(connection.id)?.();
    this.authWatchers.delete(connection.id);
    const userConnectionIds = this.userConnections.get(state.userId);
    if (!userConnectionIds) {
      return;
    }

    userConnectionIds.delete(connection.id);
    if (userConnectionIds.size > 0) {
      return;
    }

    this.userConnections.delete(state.userId);
    this.broadcastEvent({
      type: "presence_leave",
      payload: {
        userId: state.userId,
      },
    });

    if (this.userConnections.size === 0 && !this.activeStream) {
      this.clearSynthesisRetryTimer();
      this.sessionSnapshot = null;
      this.sessionFetchPromise = null;
    }
  }

  private toPresence(userId: string): ThinkingSessionPresence {
    const connectionIds = this.userConnections.get(userId) ?? new Set<string>();
    const firstConnectionId = connectionIds.values().next().value as string | undefined;
    const state = firstConnectionId ? this.connections.get(firstConnectionId) : null;

    return {
      userId,
      email: state?.email ?? "",
      name: state?.name ?? "Collaborator",
      color: state?.color ?? getCollaboratorColor(userId),
      canParticipate: Boolean(state?.canParticipate),
      canControl: Boolean(state?.canControl),
      isHost: Boolean(state?.isHost),
      connectedAt: state?.connectedAt ?? new Date().toISOString(),
      connectionCount: connectionIds.size,
    };
  }

  private getPresenceSnapshot(): ThinkingSessionPresence[] {
    return Array.from(this.userConnections.keys())
      .map((userId) => this.toPresence(userId))
      .sort((left, right) => left.name.localeCompare(right.name));
  }

  private async ensureSessionSnapshot(token: string): Promise<ThinkingSession> {
    if (this.sessionSnapshot) {
      return this.getHydratedSessionSnapshot();
    }

    if (!this.sessionFetchPromise) {
      this.sessionFetchPromise = this.fetchSessionSnapshot(token)
        .then((snapshot) => {
          this.sessionSnapshot = snapshot;
          return snapshot;
        })
        .finally(() => {
          this.sessionFetchPromise = null;
        });
    }

    const snapshot = await this.sessionFetchPromise;
    return this.getHydratedSessionSnapshot(snapshot);
  }

  private getHydratedSessionSnapshot(snapshot = this.sessionSnapshot): ThinkingSession {
    if (!snapshot) {
      throw new Error("Thinking session snapshot is unavailable");
    }

    if (
      this.activeStream
      && snapshot.activeSynthesisRun
      && snapshot.activeSynthesisRun.id === this.activeStream.runId
    ) {
      return {
        ...snapshot,
        synthesisOutput: this.activeStream.fullText,
        activeSynthesisRun: {
          ...snapshot.activeSynthesisRun,
          outputText: this.activeStream.fullText,
        },
        synthesisRuns: snapshot.synthesisRuns.map((run) => (
          run.id === this.activeStream?.runId
            ? { ...run, outputText: this.activeStream.fullText }
            : run
        )),
      };
    }

    return snapshot;
  }

  private async fetchSessionSnapshot(token: string): Promise<ThinkingSession> {
    const response = await fetchJson<unknown>(
      `${this.backendUrl}/thinking-sessions/${this.sessionId}`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      },
    );
    return transformThinkingSessionFromAPI(response);
  }

  private async pingParticipant(token: string): Promise<void> {
    const response = await fetchJson<unknown>(
      `${this.backendUrl}/thinking-sessions/${this.sessionId}/participants/ping`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
        },
      },
    );
    this.sessionSnapshot = transformThinkingSessionFromAPI(response);
  }

  private async createContribution(token: string, content: string): Promise<ThinkingSession> {
    const response = await fetchJson<unknown>(
      `${this.backendUrl}/thinking-sessions/${this.sessionId}/contributions`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ content }),
      },
    );
    const snapshot = transformThinkingSessionFromAPI(response);
    this.sessionSnapshot = snapshot;
    return snapshot;
  }

  private async toggleVote(token: string, contributionId: string): Promise<ThinkingSession> {
    const response = await fetchJson<unknown>(
      `${this.backendUrl}/thinking-sessions/${this.sessionId}/votes`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ contribution_id: contributionId }),
      },
    );
    const snapshot = transformThinkingSessionFromAPI(response);
    this.sessionSnapshot = snapshot;
    return snapshot;
  }

  private async updateRefinement(token: string, refinedOutput: string): Promise<ThinkingSession> {
    const response = await fetchJson<unknown>(
      `${this.backendUrl}/thinking-sessions/${this.sessionId}/refinement`,
      {
        method: "PATCH",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ refined_output: refinedOutput }),
      },
    );
    const snapshot = transformThinkingSessionFromAPI(response);
    this.sessionSnapshot = snapshot;
    return snapshot;
  }

  private async transitionPhase(
    token: string,
    targetPhase: ThinkingSessionPhase,
  ): Promise<BackendThinkingTransitionResponse> {
    const result = await fetchJson<BackendThinkingTransitionResponse>(
      `${this.backendUrl}/thinking-sessions/${this.sessionId}/transitions`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ target_phase: targetPhase }),
      },
    );
    const session = transformThinkingSessionFromAPI(result.session);
    this.sessionSnapshot = session;
    return {
      session,
      synthesis_run_id: result.synthesis_run_id,
    };
  }

  private broadcastSessionUpdate(session: ThinkingSession): void {
    this.sessionSnapshot = session;
    this.broadcastEvent({
      type: "session_updated",
      payload: {
        session: this.getHydratedSessionSnapshot(session),
      },
    });
  }

  private startSynthesisStream(runId: string, token: string, retryCount = 0): void {
    if (this.activeStream?.runId === runId) {
      return;
    }

    if (this.activeStream && this.activeStream.runId !== runId) {
      return;
    }

    this.clearSynthesisRetryTimer();
    this.activeStream = {
      runId,
      token,
      fullText: this.sessionSnapshot?.synthesisOutput ?? "",
      persistTimer: null,
      persistInFlight: null,
      pendingPersistText: this.sessionSnapshot?.synthesisOutput ?? "",
      retryCount,
    };

    void this.consumeSynthesisStream(runId, token);
  }

  private ensurePendingSynthesisStream(preferredToken?: string): void {
    const snapshot = this.sessionSnapshot;
    if (!snapshot || snapshot.phase !== "synthesizing" || this.activeStream || this.synthesisRetryTimer) {
      return;
    }

    const activeRunId = snapshot.activeSynthesisRunId ?? null;
    if (!activeRunId) {
      return;
    }

    const activeRun = snapshot.activeSynthesisRun
      && snapshot.activeSynthesisRun.id === activeRunId
      ? snapshot.activeSynthesisRun
      : snapshot.synthesisRuns.find((run) => run.id === activeRunId) ?? null;

    if (!activeRun || !["pending", "streaming"].includes(activeRun.status)) {
      return;
    }

    const preferredState = preferredToken
      ? Array.from(this.connections.values()).find((state) => state.token === preferredToken && state.canControl)
      : null;
    const controllingState = preferredState
      ?? Array.from(this.connections.values()).find((state) => state.canControl)
      ?? null;

    if (!controllingState?.token) {
      return;
    }

    this.startSynthesisStream(activeRunId, controllingState.token);
  }

  private clearSynthesisRetryTimer(): void {
    if (!this.synthesisRetryTimer) {
      return;
    }

    clearTimeout(this.synthesisRetryTimer);
    this.synthesisRetryTimer = null;
  }

  private schedulePendingSynthesisRetry(
    runId: string,
    token: string,
    retryCount: number,
  ): void {
    if (retryCount >= MAX_SYNTHESIS_STREAM_RETRIES) {
      this.broadcastEvent({
        type: "server_error",
        payload: {
          code: "synthesis_stream_failed",
          message: "Synthesis stream could not be reconnected",
        },
      });
      return;
    }

    if (this.synthesisRetryTimer) {
      return;
    }

    this.synthesisRetryTimer = setTimeout(() => {
      this.synthesisRetryTimer = null;
      if (
        this.sessionSnapshot?.phase !== "synthesizing"
        || this.sessionSnapshot.activeSynthesisRunId !== runId
        || this.activeStream
      ) {
        return;
      }

      this.startSynthesisStream(runId, token, retryCount + 1);
    }, SYNTHESIS_STREAM_RETRY_DELAY_MS);
  }

  private async consumeSynthesisStream(runId: string, token: string): Promise<void> {
    try {
      const response = await fetch(
        `${this.backendUrl}/thinking-sessions/${this.sessionId}/synthesis/stream`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ run_id: runId }),
        },
      );

      if (!response.ok || !response.body) {
        const errorText = await response.text().catch(() => "Unable to start synthesis");
        throw new Error(errorText || "Unable to start synthesis");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

        const parsed = parseSseEvents(buffer);
        buffer = parsed.remainder;

        for (const event of parsed.events) {
          await this.handleSynthesisEvent(event, runId);
        }

        if (done) {
          break;
        }
      }

      if (buffer.trim()) {
        const parsed = parseSseEvents(`${buffer}\n\n`);
        for (const event of parsed.events) {
          await this.handleSynthesisEvent(event, runId);
        }
      }
    } catch {
      const message = "Synthesis stream interrupted; reconnect to recover committed progress";
      await this.flushPendingSynthesisProgress();

      const snapshot = await this.fetchSessionSnapshot(token).catch(() => this.sessionSnapshot);
      if (snapshot) {
        this.sessionSnapshot = snapshot;
        const hydratedSnapshot = this.getHydratedSessionSnapshot(snapshot);

        if (snapshot.phase === "refining" || snapshot.phase === "completed") {
          this.broadcastEvent({
            type: "synthesis_completed",
            payload: {
              runId,
              text: snapshot.finalOutput || snapshot.refinedOutput || snapshot.synthesisOutput,
              session: hydratedSnapshot,
            },
          });
        } else if (snapshot.phase === "synthesizing") {
          this.broadcastEvent({
            type: "session_updated",
            payload: {
              session: hydratedSnapshot,
            },
          });
        } else {
          this.broadcastEvent({
            type: "synthesis_failed",
            payload: {
              runId,
              message,
              session: hydratedSnapshot,
            },
          });
        }
      } else {
        this.broadcastEvent({
          type: "server_error",
          payload: {
            code: "synthesis_stream_failed",
            message,
          },
        });
      }
    } finally {
      const retryCount = this.activeStream?.retryCount ?? 0;
      const shouldRetryPendingRun = (
        this.sessionSnapshot?.phase === "synthesizing"
        && this.sessionSnapshot.activeSynthesisRunId === runId
      );

      if (this.activeStream?.runId === runId) {
        if (this.activeStream.persistTimer) {
          clearTimeout(this.activeStream.persistTimer);
        }
        this.activeStream = null;
      }

      if (shouldRetryPendingRun) {
        this.schedulePendingSynthesisRetry(runId, token, retryCount);
      }
    }
  }

  private async handleSynthesisEvent(event: unknown, runId: string): Promise<void> {
    if (!isRecord(event) || typeof event.type !== "string") {
      return;
    }

    if (isString(event.run_id) && event.run_id !== runId) {
      return;
    }

    if (event.type === "start") {
      this.broadcastEvent({
        type: "synthesis_started",
        payload: {
          runId,
          sessionId: this.sessionId,
          model: isString(event.model) ? event.model : undefined,
        },
      });
      return;
    }

    if (event.type === "token") {
      const text = isString(event.text) ? event.text : "";
      if (!text || !this.activeStream || this.activeStream.runId !== runId) {
        return;
      }

      this.activeStream.fullText = isString(event.full_text) ? event.full_text : this.activeStream.fullText + text;
      this.activeStream.pendingPersistText = this.activeStream.fullText;
      if (this.sessionSnapshot) {
        this.sessionSnapshot = {
          ...this.sessionSnapshot,
          synthesisOutput: this.activeStream.fullText,
          activeSynthesisRun: this.sessionSnapshot.activeSynthesisRun
            && this.sessionSnapshot.activeSynthesisRun.id === runId
            ? {
                ...this.sessionSnapshot.activeSynthesisRun,
                outputText: this.activeStream.fullText,
              }
            : this.sessionSnapshot.activeSynthesisRun,
          synthesisRuns: this.sessionSnapshot.synthesisRuns.map((run) => (
            run.id === runId
              ? { ...run, outputText: this.activeStream!.fullText }
              : run
          )),
        };
      }

      this.broadcastEvent({
        type: "synthesis_chunk",
        payload: {
          runId,
          sessionId: this.sessionId,
          text,
          fullText: this.activeStream.fullText,
        },
      });
      return;
    }

    if (event.type === "done" && isRecord(event.session)) {
      await this.flushPendingSynthesisProgress();
      this.sessionSnapshot = transformThinkingSessionFromAPI(event.session);
      this.broadcastEvent({
        type: "synthesis_completed",
        payload: {
          runId,
          text: isString(event.text) ? event.text : this.activeStream?.fullText ?? "",
          session: this.getHydratedSessionSnapshot(this.sessionSnapshot),
        },
      });
      return;
    }

    if (event.type === "error" && isRecord(event.session)) {
      await this.flushPendingSynthesisProgress();
      this.sessionSnapshot = transformThinkingSessionFromAPI(event.session);
      this.broadcastEvent({
        type: "synthesis_failed",
        payload: {
          runId,
          message: isString(event.message) ? event.message : "Synthesis failed",
          session: this.getHydratedSessionSnapshot(this.sessionSnapshot),
        },
      });
    }
  }

  private async flushPendingSynthesisProgress(): Promise<void> {
    // PostgreSQL progress is committed by the durable synthesis producer.
  }

  private sendToConnection(connection: Connection, message: ThinkingSessionServerMessage): void {
    connection.send(serializeThinkingSessionMessage(message));
  }

  private broadcastEvent(message: ThinkingSessionServerMessage, without: string[] = []): void {
    super.broadcast(serializeThinkingSessionMessage(message), without);
  }

  private sendError(connection: Connection, code: string, message: string): void {
    this.sendToConnection(connection, {
      type: "server_error",
      payload: {
        code,
        message,
      },
    });
  }
}

export default ThinkingSessionServer;
