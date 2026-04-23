import type * as Party from "partykit/server";
import { onConnect } from "y-partykit";
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
    env.PARTYKIT_BACKEND_URL ?? DEFAULT_PARTYKIT_BACKEND_URL,
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

async function fetchJson<T>(
  input: string,
  init: RequestInit,
  timeoutMs = COLLABORATION_AUTH_TIMEOUT_MS,
): Promise<T> {
  const maxRetries = 3;
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
        const errorText = await response.text().catch(() => "");
        throw new Error(`Backend request failed: ${response.status} ${errorText.slice(0, 100)}`);
      }

      return (await response.json()) as T;
    } catch (error) {
      clearTimeout(timeout);
      lastError = error instanceof Error ? error : new Error(String(error));

      // Don't retry on client errors (4xx) or if this is the last attempt
      if (error instanceof Error && error.message.includes("status 4")) {
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
 * One PartyKit room per note.
 * Binary Yjs sync is delegated to y-partykit, while string messages implement
 * note-specific presence, cursor, selection, and typing signals.
 */
export class NoteCollaborationServer implements Party.Server {
  private readonly connections = new Map<string, ConnectionPresenceState>();
  private readonly userConnections = new Map<string, Set<string>>();
  private readonly typingTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private readonly typingConnections = new Map<string, Set<string>>();

  constructor(readonly room: Party.Room) {}

  readonly options = {
    hibernate: false,
  };

  static async onBeforeConnect(
    request: Party.Request,
    lobby: Party.Lobby,
  ): Promise<Party.Request | Response> {
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
      request.headers.set(HEADER_USER_EMAIL, presence.email);
      request.headers.set(HEADER_USER_NAME, presence.name);
      request.headers.set(HEADER_USER_COLOR, presence.color);
      request.headers.set(HEADER_CAN_UPDATE, presence.canUpdate ? "1" : "0");
      request.headers.set(HEADER_CONNECTED_AT, presence.connectedAt);
      request.headers.set(HEADER_AUTH_TOKEN, token);

      return request;
    } catch (error) {
      const errorMsg = error instanceof Error ? error.message : String(error);
      return new Response(`Unauthorized: ${errorMsg}`, { status: 401 });
    }
  }

  async onConnect(
    connection: Party.Connection,
    ctx: Party.ConnectionContext,
  ): Promise<void> {
    const presence = this.readPresenceFromHeaders(connection, ctx.request.headers);
    const token = ctx.request.headers.get(HEADER_AUTH_TOKEN)?.trim() || "";

    const isFirstConnectionForUser = this.registerConnection(connection, presence);

    await onConnect(connection, this.room, {
      load: async () => this.loadInitialDocument(token),
      persist: { mode: "snapshot" },
      readOnly: !presence.canUpdate,
    });

    this.sendToConnection(connection, {
      type: "presence_snapshot",
      payload: {
        collaborators: this.getCollaboratorsSnapshot(),
      },
    });

    if (isFirstConnectionForUser) {
      this.broadcast({
        type: "presence_join",
        payload: {
          collaborator: this.toPresence(presence.userId),
        },
      }, [connection.id]);
    }
  }

  async onMessage(
    message: string | ArrayBuffer | ArrayBufferView,
    sender: Party.Connection,
  ): Promise<void> {
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
      this.broadcast({
        type: "cursor_move",
        payload: {
          collaborator: toIdentity(presence),
          cursor: parsed.payload,
        },
      }, [sender.id]);
      return;
    }

    if (parsed.type === "selection_change") {
      this.broadcast({
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

  async onClose(connection: Party.Connection): Promise<void> {
    this.unregisterConnection(connection);
  }

  async onError(connection: Party.Connection): Promise<void> {
    this.unregisterConnection(connection);
  }

  private readPresenceFromHeaders(
    connection: Party.Connection,
    headers: Pick<Headers, "get">,
  ): ConnectionPresenceState {
    return {
      connectionId: connection.id,
      userId: headers.get(HEADER_USER_ID)?.trim() || connection.id,
      email: headers.get(HEADER_USER_EMAIL)?.trim() || "",
      name: headers.get(HEADER_USER_NAME)?.trim() || "Unknown collaborator",
      color: headers.get(HEADER_USER_COLOR)?.trim() || getCollaboratorColor(connection.id),
      canUpdate: headers.get(HEADER_CAN_UPDATE) === "1",
      connectedAt: headers.get(HEADER_CONNECTED_AT)?.trim() || new Date().toISOString(),
    };
  }

  private async loadInitialDocument(token: string) {
    if (!token) {
      return null;
    }

    try {
      const note = await fetchJson<BackendNoteResponse>(
        `${getBackendUrl(this.room.env)}/notes/${getNoteIdFromRoomId(this.room.id)}`,
        {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        },
      );

      return createYDocFromPlainText(note.content ?? "");
    } catch {
      return null;
    }
  }

  private registerConnection(
    connection: Party.Connection,
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

  private unregisterConnection(connection: Party.Connection): void {
    const presence = this.connections.get(connection.id);
    if (!presence) {
      return;
    }

    this.clearTypingTimeout(connection.id);
    this.setTypingConnectionState(presence.userId, connection.id, false);
    this.connections.delete(connection.id);

    const userConnectionIds = this.userConnections.get(presence.userId);
    if (!userConnectionIds) {
      return;
    }

    userConnectionIds.delete(connection.id);
    if (userConnectionIds.size > 0) {
      return;
    }

    this.userConnections.delete(presence.userId);
    this.broadcast({
      type: "presence_leave",
      payload: {
        userId: presence.userId,
      },
    });
  }

  private handleTypingState(
    sender: Party.Connection,
    presence: ConnectionPresenceState,
    isTyping: boolean,
  ): void {
    const typingStateChanged = this.setTypingConnectionState(
      presence.userId,
      sender.id,
      isTyping,
    );

    if (typingStateChanged) {
      this.broadcast({
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
        this.broadcast({
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
    connection: Party.Connection,
    message: CollaborationServerMessage,
  ): void {
    connection.send(serializeCollaborationMessage(message));
  }

  private broadcast(
    message: CollaborationServerMessage,
    without: string[] = [],
  ): void {
    this.room.broadcast(serializeCollaborationMessage(message), without);
  }

  private sendError(
    connection: Party.Connection,
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
