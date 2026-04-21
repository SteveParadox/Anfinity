import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type * as Party from "partykit/server";
import { COLLABORATION_TYPING_TIMEOUT_MS } from "../src/lib/collaboration/constants";
import NoteCollaborationServer from "./collaboration-server";

const { onConnectMock } = vi.hoisted(() => ({
  onConnectMock: vi.fn(async () => {}),
}));

vi.mock("y-partykit", () => ({
  onConnect: onConnectMock,
}));

class FakeConnection {
  readonly sent: string[] = [];
  readonly listeners = new Map<string, Array<(...args: unknown[]) => void>>();
  readonly close = vi.fn();
  readonly send = vi.fn((message: string) => {
    this.sent.push(message);
  });
  readonly setState = vi.fn();
  readonly readyState = 1;

  constructor(readonly id: string) {}

  addEventListener(event: string, listener: (...args: unknown[]) => void) {
    const current = this.listeners.get(event) ?? [];
    current.push(listener);
    this.listeners.set(event, current);
  }
}

function createRoom(): Party.Room {
  return {
    id: "note-123",
    env: {
      PARTYKIT_BACKEND_URL: "http://backend.test",
    },
    broadcast: vi.fn(),
    storage: {},
  } as unknown as Party.Room;
}

function createContextForUser(userId: string, canUpdate = true) {
  const headers = new Headers();
  headers.set("x-collab-user-id", userId);
  headers.set("x-collab-user-email", `${userId}@example.com`);
  headers.set("x-collab-user-name", userId);
  headers.set("x-collab-user-color", "#F5E642");
  headers.set("x-collab-can-update", canUpdate ? "1" : "0");
  headers.set("x-collab-connected-at", "2026-04-21T00:00:00.000Z");
  headers.set("x-collab-auth-token", "token-value");

  return {
    request: {
      headers,
    },
  } as unknown as Party.ConnectionContext;
}

function parseSentMessage(connection: FakeConnection) {
  const raw = connection.sent.at(-1);
  return raw ? JSON.parse(raw) : null;
}

describe("NoteCollaborationServer", () => {
  beforeEach(() => {
    vi.useRealTimers();
    onConnectMock.mockClear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("authorizes and decorates the request in onBeforeConnect", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        note_id: "note-123",
        access_source: "workspace",
        can_view: true,
        can_update: true,
        can_delete: true,
        can_manage: false,
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "user-1",
        email: "user@example.com",
        full_name: "User One",
      }), { status: 200 }));

    vi.stubGlobal("fetch", fetchMock);

    const request = {
      url: "https://collab.example.com/parties/main/note-123?token=test-token",
      headers: new Headers(),
    } as unknown as Party.Request;

    const result = await NoteCollaborationServer.onBeforeConnect(request, {
      env: {
        PARTYKIT_BACKEND_URL: "http://backend.test",
      },
    } as unknown as Party.Lobby);

    expect(result).toBe(request);
    expect(request.headers.get("x-collab-user-id")).toBe("user-1");
    expect(request.headers.get("x-collab-can-update")).toBe("1");
    expect(request.headers.get("x-collab-auth-token")).toBe("test-token");
  });

  it("rejects onBeforeConnect when note access cannot view the room", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        note_id: "note-123",
        access_source: "none",
        can_view: false,
        can_update: false,
        can_delete: false,
        can_manage: false,
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "user-1",
        email: "user@example.com",
      }), { status: 200 }));

    vi.stubGlobal("fetch", fetchMock);

    const request = {
      url: "https://collab.example.com/parties/main/note-123?token=test-token",
      headers: new Headers(),
    } as unknown as Party.Request;

    const result = await NoteCollaborationServer.onBeforeConnect(request, {
      env: {
        PARTYKIT_BACKEND_URL: "http://backend.test",
      },
    } as unknown as Party.Lobby);

    expect(result).toBeInstanceOf(Response);
    expect((result as Response).status).toBe(401);
  });

  it("sends a server error for malformed collaboration messages", async () => {
    const room = createRoom();
    const server = new NoteCollaborationServer(room);
    const connection = new FakeConnection("conn-1");

    await server.onConnect(connection as unknown as Party.Connection, createContextForUser("user-1"));
    connection.sent.length = 0;

    await server.onMessage("{bad json", connection as unknown as Party.Connection);

    expect(parseSentMessage(connection)).toMatchObject({
      type: "server_error",
      payload: {
        code: "invalid_message",
      },
    });
  });

  it("clears presence only when the last connection for a user closes", async () => {
    const room = createRoom();
    const server = new NoteCollaborationServer(room);
    const first = new FakeConnection("conn-1");
    const second = new FakeConnection("conn-2");

    await server.onConnect(first as unknown as Party.Connection, createContextForUser("user-1"));
    await server.onConnect(second as unknown as Party.Connection, createContextForUser("user-1"));
    vi.mocked(room.broadcast).mockClear();

    await server.onClose(first as unknown as Party.Connection);
    expect(room.broadcast).not.toHaveBeenCalled();

    await server.onClose(second as unknown as Party.Connection);
    expect(room.broadcast).toHaveBeenCalledWith(
      expect.stringContaining('"type":"presence_leave"'),
      [],
    );
  });

  it("broadcasts typing start and auto-timeout stop events", async () => {
    vi.useFakeTimers();

    const room = createRoom();
    const server = new NoteCollaborationServer(room);
    const first = new FakeConnection("conn-1");
    const second = new FakeConnection("conn-2");

    await server.onConnect(first as unknown as Party.Connection, createContextForUser("user-1"));
    await server.onConnect(second as unknown as Party.Connection, createContextForUser("user-2"));
    vi.mocked(room.broadcast).mockClear();

    await server.onMessage(
      JSON.stringify({
        type: "typing_indicator",
        payload: { isTyping: true },
      }),
      first as unknown as Party.Connection,
    );

    expect(room.broadcast).toHaveBeenCalledWith(
      expect.stringContaining('"type":"typing_indicator"'),
      ["conn-1"],
    );

    vi.advanceTimersByTime(COLLABORATION_TYPING_TIMEOUT_MS + 10);

    expect(room.broadcast).toHaveBeenLastCalledWith(
      expect.stringContaining('"isTyping":false'),
      ["conn-1"],
    );
  });

  it("tracks typing per user across multiple connections without sending stale stop events", async () => {
    const room = createRoom();
    const server = new NoteCollaborationServer(room);
    const first = new FakeConnection("conn-1");
    const second = new FakeConnection("conn-2");
    const observer = new FakeConnection("conn-3");

    await server.onConnect(first as unknown as Party.Connection, createContextForUser("user-1"));
    await server.onConnect(second as unknown as Party.Connection, createContextForUser("user-1"));
    await server.onConnect(observer as unknown as Party.Connection, createContextForUser("user-2"));
    vi.mocked(room.broadcast).mockClear();

    await server.onMessage(
      JSON.stringify({
        type: "typing_indicator",
        payload: { isTyping: true },
      }),
      first as unknown as Party.Connection,
    );

    expect(room.broadcast).toHaveBeenCalledTimes(1);

    await server.onMessage(
      JSON.stringify({
        type: "typing_indicator",
        payload: { isTyping: true },
      }),
      second as unknown as Party.Connection,
    );

    expect(room.broadcast).toHaveBeenCalledTimes(1);

    await server.onMessage(
      JSON.stringify({
        type: "typing_indicator",
        payload: { isTyping: false },
      }),
      first as unknown as Party.Connection,
    );

    expect(room.broadcast).toHaveBeenCalledTimes(1);

    await server.onClose(first as unknown as Party.Connection);
    expect(room.broadcast).toHaveBeenCalledTimes(1);

    await server.onClose(second as unknown as Party.Connection);
    expect(room.broadcast).toHaveBeenCalledWith(
      expect.stringContaining('"type":"presence_leave"'),
      [],
    );
  });
});
