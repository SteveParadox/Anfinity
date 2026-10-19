import { loadSnapshot, saveSnapshot } from "./snapshot";
import type { Connection, ConnectionContext, WSMessage } from "partyserver";
import { YServer } from "y-partyserver";
import * as Y from "yjs";
import { yDocToPlainText } from "../src/lib/collaboration/content";
import type { RealtimeEnv } from "./security";
import { authorizeMessage, checkMessageBudget, watchAuthorization, decodeIdentityHeader } from "./security";
import {
  COLLABORATION_AUTH_TIMEOUT_MS,
  COLLABORATION_TYPING_TIMEOUT_MS,
  DEFAULT_PARTYKIT_BACKEND_URL,
  NOTE_ROOM_PREFIX,
} from "../src/lib/collaboration/constants";
import { getCollaboratorColor } from "../src/lib/collaboration/colors";
import { createYDocFromPlainText } from "../src/lib/collaboration/content";
import {
  type CollaboratorIdentity,
  type CollaboratorPresence,
  type CollaborationClientMessage,
  type CollaborationServerMessage,
  serializeCollaborationMessage,
} from "../src/lib/collaboration/protocol";

const HEADER_USER_ID = "x-collab-user-id";
const HEADER_USER_EMAIL = "x-collab-user-email";
const HEADER_USER_NAME = "x-collab-user-name";
const HEADER_USER_COLOR = "x-collab-user-color";
const HEADER_CAN_UPDATE = "x-collab-can-update";
const HEADER_CONNECTED_AT = "x-collab-connected-at";
const HEADER_AUTH_TOKEN = "x-collab-auth-token";
const MAX_COLLABORATION_MESSAGE_BYTES = 16_384;
const MAX_DOCUMENT_POSITION = 1_000_000;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type BackendNoteResponse = {
  id: string;
  workspace_id?: string | null;
  content?: string | null;
};

type BackendNoteAccessResponse = {
  note_id: string;
  access_source: string;
  can_view: boolean;
  can_update: boolean;
  can_delete: boolean;
  can_manage: boolean;
  collaborator_role?: "viewer" | "editor" | null;
};

type BackendUserResponse = {
  id: string;
  email: string;
  full_name?: string | null;
};

type ConnectionPresenceState = CollaboratorIdentity & {
  connectionId: string;
  connectedAt: string;
};

function getBackendUrl(env: Record<string, unknown>): string {
  const value = String(
    env.REALTIME_BACKEND_URL ?? env.PARTYKIT_BACKEND_URL ?? DEFAULT_PARTYKIT_BACKEND_URL,
  ).trim();

  return value.replace(/\/+$/, "");
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

function getNoteIdFromRoomId(roomId: string): string {
  return roomId.startsWith(NOTE_ROOM_PREFIX)
    ? roomId.slice(NOTE_ROOM_PREFIX.length).trim()
    : roomId.trim();
}

function isValidEntityId(value: string): boolean {
  return UUID_PATTERN.test(value);
}

function getBearerToken(requestUrl: string): string {
  const url = new URL(requestUrl);
  return url.searchParams.get("token")?.trim() ?? "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value));
}

function isNonNegativeIntegerOrNull(value: unknown): value is number | null {
  return (
    value === null
    || (
      typeof value === "number"
      && Number.isInteger(value)
      && value >= 0
      && value <= MAX_DOCUMENT_POSITION
    )
  );
}

function parseClientMessage(rawMessage: string): CollaborationClientMessage | null {
  if (rawMessage.length > MAX_COLLABORATION_MESSAGE_BYTES) {
    return null;
  }

  try {
    const parsed = JSON.parse(rawMessage) as unknown;

    if (!isRecord(parsed) || typeof parsed.type !== "string" || !isRecord(parsed.payload)) {
      return null;
    }

    if (parsed.type === "cursor_move") {
      const payload = parsed.payload;

      if (
        !isNonNegativeIntegerOrNull(payload.position)
        || !isNonNegativeIntegerOrNull(payload.anchor)
        || !isNonNegativeIntegerOrNull(payload.head)
        || !isNullableNumber(payload.clientX)
        || !isNullableNumber(payload.clientY)
      ) {
        return null;
      }

      return {
        type: "cursor_move",
        payload: {
          position: payload.position,
          anchor: payload.anchor,
          head: payload.head,
          clientX: payload.clientX,
          clientY: payload.clientY,
        },
      };
    }

    if (parsed.type === "selection_change") {
      const payload = parsed.payload;

      if (
        !isNonNegativeIntegerOrNull(payload.from)
        || !isNonNegativeIntegerOrNull(payload.to)
        || typeof payload.empty !== "boolean"
      ) {
        return null;
      }

      if (
        payload.from !== null
        && payload.to !== null
        && payload.from > payload.to
      ) {
        return null;
      }

      return {
        type: "selection_change",
        payload: {
          from: payload.from,
          to: payload.to,
          empty: payload.empty,
        },
      };
    }

    if (parsed.type === "typing_indicator") {
      if (typeof parsed.payload.isTyping !== "boolean") {
        return null;
      }

      return {
        type: "typing_indicator",
        payload: {
          isTyping: parsed.payload.isTyping,
        },
      };
    }
  } catch {
    return null;
  }

  return null;
}

class BackendResponseError extends Error {
  constructor(readonly status: number) { super(`Backend request failed: ${status}`); }
}

async function fetchJson<T>(
  input: string,
  init: RequestInit,
  timeoutMs = COLLABORATION_AUTH_TIMEOUT_MS,
): Promise<T> {
  const maxRetries = (init.method && init.method !== "GET") ? 0 : 2;
  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetch(input, {
        ...init,
        signal: controller.signal,
      });

      clearTimeout(timeout);

      if (!response.ok) {
        throw new BackendResponseError(response.status);
      }

      return (await response.json()) as T;
    } catch (error) {
      clearTimeout(timeout);
      lastError = error instanceof Error ? error : new Error(String(error));

      // Don't retry on client errors (4xx) or if this is the last attempt
      if (error instanceof Error && /failed: 4\d\d/.test(error.message)) {
        throw error;
      }

      if (attempt < maxRetries) {
        // Exponential backoff: 100ms, 200ms, 400ms
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
  noteId: string,
): Promise<ConnectionPresenceState> {
  const headers = {
    Authorization: `Bearer ${token}`,
  };

  const [noteAccess, currentUser] = await Promise.all([
    fetchJson<BackendNoteAccessResponse>(`${backendUrl}/notes/${noteId}/access`, { headers }),
    fetchJson<BackendUserResponse>(`${backendUrl}/auth/me`, { headers }),
  ]);

  if (!noteAccess.can_view) {
    throw new Error("Missing note view permission");
  }

  return {
    connectionId: "",
    userId: currentUser.id,
    email: currentUser.email,
    name: currentUser.full_name?.trim() || currentUser.email,
    color: getCollaboratorColor(currentUser.id),
    canUpdate: Boolean(noteAccess.can_update),
    connectedAt: new Date().toISOString(),
  };
}

function toIdentity(state: ConnectionPresenceState): CollaboratorIdentity {
  return {
    userId: state.userId,
    email: state.email,
    name: state.name,
    color: state.color,
    canUpdate: state.canUpdate,
  };
}

/**
 * One Durable Object per note.
 * Binary Yjs sync is delegated to YServer, while string messages implement
 * note-specific presence, cursor, selection, and typing signals.
 */
export class NoteCollaborationServer extends YServer<RealtimeEnv> {
  private readonly connections = new Map<string, ConnectionPresenceState>();
  private readonly userConnections = new Map<string, Set<string>>();
  private readonly typingTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private readonly typingConnections = new Map<string, Set<string>>();

  private readonly authWatchers = new Map<string, () => void>();
  private initialized = false;
  private initializePromise: Promise<void> | null = null;
  private saveChain: Promise<void> = Promise.resolve();
  private messageChain: Promise<void> = Promise.resolve();
  private pendingMessages = 0;
  private backendContent = "";
  private projectionToken = "";
  private revision = 0;
  private persistedRevision = -1;
  private persistedBackendContent = "";

  async onLoad(): Promise<void> {
    const saved = await loadSnapshot(this.ctx.storage, this.document);
    if (saved) {
      this.backendContent = saved.backendContent;
      this.initialized = true;
      this.persistedRevision = 0;
      this.persistedBackendContent = this.backendContent;
    }
    this.document.on("update", () => { this.revision++; });
  }

  private async checkpoint(): Promise<void> {
    if (!this.initialized || (this.persistedRevision === this.revision && this.persistedBackendContent === this.backendContent)) return;
    const revision = this.revision;
    const backendContent = this.backendContent;
    await saveSnapshot(this.ctx.storage, this.document, backendContent);
    this.persistedRevision = revision;
    this.persistedBackendContent = backendContent;
  }

  isReadOnly(connection: Connection): boolean {
    return !this.connections.get(connection.id)?.canUpdate;
  }

  async onSave(): Promise<void> {
    // Serialize projections: a slow request must never overwrite a newer save.
    const save = async () => {
      await this.checkpoint();
      const content = yDocToPlainText(this.document);
      if (!this.initialized || content === this.backendContent.replace(/\r\n/g, "\n") || !this.projectionToken) return;
      try {
        const tokens = [...new Set([
          ...[...this.connections.entries()].filter(([, presence]) => presence.canUpdate)
            .map(([id]) => this.connectionTokens.get(id)).filter((token): token is string => Boolean(token)),
          this.projectionToken,
        ])];
        let saved = false;
        for (const token of tokens) {
          try {
            const note = await fetchJson<BackendNoteResponse>(
              `${getBackendUrl(this.env)}/notes/${getNoteIdFromRoomId(this.name)}/collaboration`,
              { method: "PATCH", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
                body: JSON.stringify({ content, base_content: this.backendContent }) },
            );
            this.projectionToken = token;
            this.backendContent = note.content ?? content;
            saved = true;
            break;
          } catch (error) {
            // Authentication/permission failures cannot have committed a mutation.
            if (!(error instanceof BackendResponseError) || ![401, 403].includes(error.status)) throw error;
          }
        }
        if (!saved) throw new Error("No authorized editor credential available");
        await this.checkpoint();
      } catch {
        console.error(JSON.stringify({ event: "note_projection_failed" }));
        this.broadcastEvent({ type: "server_error", payload: { code: "persistence_failed", message: "Changes are retained in collaboration storage; backend save needs recovery" } });
        throw new Error("Backend projection failed");
      }
    };
    const pending = this.saveChain.then(save, save);
    this.saveChain = pending.catch(() => undefined);
    return pending;
  }

  static options = {
    hibernate: false,
  };

  static async onBeforeConnect(
    request: Request,
    lobby: { env: RealtimeEnv },
  ): Promise<Request | Response> {
    const token = getBearerToken(request.url);
    const roomId = getRoomIdFromRequest(request.url);
    const noteId = roomId ? getNoteIdFromRoomId(roomId) : null;

    if (!token) {
      return new Response("Unauthorized: Missing token", { status: 401 });
    }

    if (!noteId) {
      return new Response("Unauthorized: Missing noteId", { status: 401 });
    }

    if (!isValidEntityId(noteId)) {
      return new Response("Unauthorized: Invalid note room id", { status: 401 });
    }

    try {
      const backendUrl = getBackendUrl(lobby.env);
      const presence = await resolveConnectionContext(backendUrl, token, noteId);

      request.headers.set(HEADER_USER_ID, presence.userId);
      request.headers.set(HEADER_USER_EMAIL, encodeURIComponent(presence.email));
      request.headers.set(HEADER_USER_NAME, encodeURIComponent(presence.name));
      request.headers.set(HEADER_USER_COLOR, presence.color);
      request.headers.set(HEADER_CAN_UPDATE, presence.canUpdate ? "1" : "0");
      request.headers.set(HEADER_CONNECTED_AT, presence.connectedAt);
      request.headers.set(HEADER_AUTH_TOKEN, token);

      return request;
    } catch {
      console.warn(JSON.stringify({ event: "note_auth_failed" }));
      return new Response("Unauthorized", { status: 401 });
    }
  }

  async onConnect(
    connection: Connection,
    ctx: ConnectionContext,
  ): Promise<void> {
    const presence = this.readPresenceFromHeaders(connection, ctx.request.headers);
    const token = ctx.request.headers.get(HEADER_AUTH_TOKEN)?.trim() || "";

    const isFirstConnectionForUser = this.registerConnection(connection, presence);
    this.connectionTokens.set(connection.id, token);
    this.authWatchers.set(connection.id, watchAuthorization(this.env, connection, token, this.name));

    if (presence.canUpdate) this.projectionToken = token;
    if (!this.initialized) {
      this.initializePromise ??= (async () => {
        const note = await fetchJson<BackendNoteResponse>(`${getBackendUrl(this.env)}/notes/${getNoteIdFromRoomId(this.name)}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        const seed = createYDocFromPlainText(note.content ?? "");
        if (seed) { Y.applyUpdate(this.document, Y.encodeStateAsUpdate(seed)); seed.destroy(); }
        this.backendContent = note.content ?? "";
        this.initialized = true;
        await this.checkpoint();
      })().catch((error) => { this.initializePromise = null; throw error; });
      await this.initializePromise;
    }
    await super.onConnect(connection, ctx);
    // A restored dirty snapshot must project even if nobody types after reopening it.
    if (presence.canUpdate) this.ctx.waitUntil(this.onSave().catch(() => undefined));

    this.sendToConnection(connection, {
      type: "presence_snapshot",
      payload: {
        collaborators: this.getCollaboratorsSnapshot(),
      },
    });

    if (isFirstConnectionForUser) {
      this.broadcastEvent({
        type: "presence_join",
        payload: {
          collaborator: this.toPresence(presence.userId)!,
        },
      }, [connection.id]);
    }
  }

  onMessage(sender: Connection, message: WSMessage): Promise<void> {
    if (!checkMessageBudget(sender, message)) return Promise.resolve();
    if (this.pendingMessages >= 32) { sender.close(4429, "Room queue full"); return Promise.resolve(); }
    this.pendingMessages++;
    const pending = this.messageChain.then(() => this.processMessage(sender, message)).finally(() => { this.pendingMessages--; });
    this.messageChain = pending.catch(() => undefined);
    return pending;
  }

  private async processMessage(sender: Connection, message: WSMessage): Promise<void> {
    const token = this.connectionTokens.get(sender.id);
    const access = token ? await authorizeMessage(this.env, token, this.name) : null;
    if (!access) {
      sender.close(4403, "Access expired or revoked"); return;
    }
    const presence = this.connections.get(sender.id);
    if (presence?.canUpdate && !access.can_update) {
      sender.close(4403, "Editing permission revoked"); return;
    }
    if (presence) presence.canUpdate = Boolean(access.can_update);
    // YServer checks isReadOnly for both sync step 2 and update frames.
    super.onMessage(sender, message);
    await this.checkpoint();
  }

  private readonly connectionTokens = new Map<string, string>();

  onCustomMessage(sender: Connection, message: string): void {
    if (typeof message !== "string") {
      return;
    }

    const presence = this.connections.get(sender.id);
    if (!presence) {
      return;
    }

    const parsed = parseClientMessage(message);
    if (!parsed) {
      this.sendError(sender, "invalid_message", "Malformed collaboration payload");
      return;
    }

    if (parsed.type === "cursor_move") {
      this.broadcastEvent({
        type: "cursor_move",
        payload: {
          collaborator: toIdentity(presence),
          cursor: parsed.payload,
        },
      }, [sender.id]);
      return;
    }

    if (parsed.type === "selection_change") {
      this.broadcastEvent({
        type: "selection_change",
        payload: {
          collaborator: toIdentity(presence),
          selection: parsed.payload,
        },
      }, [sender.id]);
      return;
    }

    if (parsed.type === "typing_indicator") {
      if (parsed.payload.isTyping && !presence.canUpdate) {
        return;
      }

      this.handleTypingState(sender, presence, parsed.payload.isTyping);
    }
  }

  async onClose(connection: Connection): Promise<void> {
    super.onClose(connection, 1000, "", true);
    this.unregisterConnection(connection);
    await this.onSave();
  }

  async onError(connection: Connection): Promise<void> {
    super.onClose(connection, 1011, "", false);
    this.unregisterConnection(connection);
  }

  private readPresenceFromHeaders(
    connection: Connection,
    headers: Pick<Headers, "get">,
  ): ConnectionPresenceState {
    return {
      connectionId: connection.id,
      userId: headers.get(HEADER_USER_ID)?.trim() || connection.id,
      email: decodeIdentityHeader(headers.get(HEADER_USER_EMAIL)).trim(),
      name: decodeIdentityHeader(headers.get(HEADER_USER_NAME)).trim() || "Unknown collaborator",
      color: headers.get(HEADER_USER_COLOR)?.trim() || getCollaboratorColor(connection.id),
      canUpdate: headers.get(HEADER_CAN_UPDATE) === "1",
      connectedAt: headers.get(HEADER_CONNECTED_AT)?.trim() || new Date().toISOString(),
    };
  }

  private registerConnection(
    connection: Connection,
    presence: ConnectionPresenceState,
  ): boolean {
    connection.setState(presence);
    this.connections.set(connection.id, presence);

    const existingConnections = this.userConnections.get(presence.userId);
    const isFirstConnection = !existingConnections || existingConnections.size === 0;
    const nextConnections = existingConnections ?? new Set<string>();
    nextConnections.add(connection.id);
    this.userConnections.set(presence.userId, nextConnections);

    return isFirstConnection;
  }

  private unregisterConnection(connection: Connection): void {
    const presence = this.connections.get(connection.id);
    if (!presence) {
      return;
    }

    this.clearTypingTimeout(connection.id);
    if (this.setTypingConnectionState(presence.userId, connection.id, false)) {
      this.broadcastEvent({ type: "typing_indicator", payload: { collaborator: toIdentity(presence), isTyping: false } });
    }
    this.connections.delete(connection.id);
    this.connectionTokens.delete(connection.id);
    this.authWatchers.get(connection.id)?.();
    this.authWatchers.delete(connection.id);

    const userConnectionIds = this.userConnections.get(presence.userId);
    if (!userConnectionIds) {
      return;
    }

    userConnectionIds.delete(connection.id);
    if (userConnectionIds.size > 0) {
      return;
    }

    this.userConnections.delete(presence.userId);
    this.broadcastEvent({
      type: "presence_leave",
      payload: {
        userId: presence.userId,
      },
    });
  }

  private handleTypingState(
    sender: Connection,
    presence: ConnectionPresenceState,
    isTyping: boolean,
  ): void {
    const typingStateChanged = this.setTypingConnectionState(
      presence.userId,
      sender.id,
      isTyping,
    );

    if (typingStateChanged) {
      this.broadcastEvent({
        type: "typing_indicator",
        payload: {
          collaborator: toIdentity(presence),
          isTyping,
        },
      }, [sender.id]);
    }

    if (!isTyping) {
      this.clearTypingTimeout(sender.id);
      return;
    }

    this.clearTypingTimeout(sender.id);
    const timeout = setTimeout(() => {
      if (!this.connections.has(sender.id)) {
        return;
      }

      const typingStateChanged = this.setTypingConnectionState(
        presence.userId,
        sender.id,
        false,
      );
      if (typingStateChanged) {
        this.broadcastEvent({
          type: "typing_indicator",
          payload: {
            collaborator: toIdentity(presence),
            isTyping: false,
          },
        }, [sender.id]);
      }
      this.typingTimers.delete(sender.id);
    }, COLLABORATION_TYPING_TIMEOUT_MS);

    this.typingTimers.set(sender.id, timeout);
  }

  private clearTypingTimeout(connectionId: string): void {
    const timeout = this.typingTimers.get(connectionId);
    if (!timeout) {
      return;
    }

    clearTimeout(timeout);
    this.typingTimers.delete(connectionId);
  }

  private setTypingConnectionState(
    userId: string,
    connectionId: string,
    isTyping: boolean,
  ): boolean {
    const currentConnections = this.typingConnections.get(userId) ?? new Set<string>();
    const wasTyping = currentConnections.size > 0;

    if (isTyping) {
      currentConnections.add(connectionId);
      this.typingConnections.set(userId, currentConnections);
    } else {
      currentConnections.delete(connectionId);
      if (currentConnections.size === 0) {
        this.typingConnections.delete(userId);
      } else {
        this.typingConnections.set(userId, currentConnections);
      }
    }

    const isUserTyping = (this.typingConnections.get(userId)?.size ?? 0) > 0;
    return wasTyping !== isUserTyping;
  }

  private getCollaboratorsSnapshot(): CollaboratorPresence[] {
    return Array.from(this.userConnections.keys())
      .map((userId) => this.toPresence(userId))
      .filter((collaborator): collaborator is CollaboratorPresence => Boolean(collaborator));
  }

  private toPresence(userId: string): CollaboratorPresence | null {
    const connectionIds = this.userConnections.get(userId);
    if (!connectionIds || connectionIds.size === 0) {
      return null;
    }

    const firstConnectionId = connectionIds.values().next().value as string | undefined;
    if (!firstConnectionId) {
      return null;
    }

    const state = this.connections.get(firstConnectionId);
    if (!state) {
      return null;
    }

    return {
      ...toIdentity(state),
      connectionCount: connectionIds.size,
      connectedAt: state.connectedAt,
    };
  }

  private sendToConnection(
    connection: Connection,
    message: CollaborationServerMessage,
  ): void {
    this.sendCustomMessage(connection, serializeCollaborationMessage(message));
  }

  private broadcastEvent(
    message: CollaborationServerMessage,
    without: string[] = [],
  ): void {
    this.broadcastCustomMessage(serializeCollaborationMessage(message), without[0] ? this.getConnection(without[0]) : undefined);
  }

  private sendError(
    connection: Connection,
    code: string,
    message: string,
  ): void {
    this.sendToConnection(connection, {
      type: "server_error",
      payload: {
        code,
        message,
      },
    });
  }
}

export default NoteCollaborationServer;
