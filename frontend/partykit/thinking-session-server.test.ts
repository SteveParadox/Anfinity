import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type * as Party from "partykit/server";
import ThinkingSessionServer from "./thinking-session-server";

const SESSION_ID = "22222222-2222-4222-8222-222222222222";
const RUN_ID = "33333333-3333-4333-8333-333333333333";

class FakeConnection {
  readonly sent: string[] = [];
  readonly close = vi.fn();
  readonly send = vi.fn((message: string) => {
    this.sent.push(message);
  });
  readonly setState = vi.fn();
  readonly readyState = 1;

  constructor(readonly id: string) {}
}

function createRoom(): Party.Room {
  return {
    id: `thinking-session:${SESSION_ID}`,
    env: {
      PARTYKIT_BACKEND_URL: "http://backend.test",
    },
    broadcast: vi.fn(),
    storage: {},
  } as unknown as Party.Room;
}

function createContextForUser(
  userId: string,
  options?: { canParticipate?: boolean; canControl?: boolean; isHost?: boolean },
) {
  const headers = new Headers();
  headers.set("x-thinking-user-id", userId);
  headers.set("x-thinking-user-email", `${userId}@example.com`);
  headers.set("x-thinking-user-name", userId);
  headers.set("x-thinking-user-color", "#7DD3FC");
  headers.set("x-thinking-can-participate", options?.canParticipate === false ? "0" : "1");
  headers.set("x-thinking-can-control", options?.canControl ? "1" : "0");
  headers.set("x-thinking-is-host", options?.isHost ? "1" : "0");
  headers.set("x-thinking-connected-at", "2026-04-21T00:00:00.000Z");
  headers.set("x-thinking-auth-token", "token-value");

  return {
    request: {
      headers,
    },
  } as unknown as Party.ConnectionContext;
}

function buildRawSynthesisRun(overrides: Record<string, unknown> = {}) {
  return {
    id: RUN_ID,
    session_id: SESSION_ID,
    triggered_by_user_id: "user-1",
    triggered_by: { id: "user-1", email: "user-1@example.com", name: "User One" },
    status: "pending",
    model: "gpt-4o",
    contribution_count: 1,
    output_text: "",
    error_message: null,
    started_at: null,
    completed_at: null,
    failed_at: null,
    created_at: "2026-04-21T00:00:00.000Z",
    updated_at: "2026-04-21T00:00:00.000Z",
    ...overrides,
  };
}

function buildRawSession(overrides: Record<string, unknown> = {}) {
  return {
    id: SESSION_ID,
    workspace_id: "workspace-1",
    note_id: null,
    room_id: `thinking-session:${SESSION_ID}`,
    title: "Weekly Synthesis",
    prompt_context: "What matters this sprint?",
    created_by_user_id: "user-1",
    host_user_id: "user-1",
    creator: { id: "user-1", email: "user-1@example.com", name: "User One" },
    host: { id: "user-1", email: "user-1@example.com", name: "User One" },
    phase: "gathering",
    phase_entered_at: "2026-04-21T00:00:00.000Z",
    waiting_started_at: "2026-04-21T00:00:00.000Z",
    gathering_started_at: "2026-04-21T00:00:00.000Z",
    synthesizing_started_at: null,
    refining_started_at: null,
    completed_at: null,
    active_synthesis_run_id: null,
    synthesis_output: "",
    refined_output: "",
    final_output: "",
    last_refined_by_user_id: null,
    last_refined_by: null,
    created_at: "2026-04-21T00:00:00.000Z",
    updated_at: "2026-04-21T00:00:00.000Z",
    participants: [],
    contributions: [],
    synthesis_runs: [],
    active_synthesis_run: null,
    ...overrides,
  };
}

function buildSynthesizingSession() {
  const activeRun = buildRawSynthesisRun();
  return buildRawSession({
    phase: "synthesizing",
    active_synthesis_run_id: RUN_ID,
    active_synthesis_run: activeRun,
    synthesis_runs: [activeRun],
  });
}

function parseLastSent(connection: FakeConnection) {
  return JSON.parse(connection.sent.at(-1) || "{}");
}

async function flushPromises() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe("ThinkingSessionServer", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("authorizes and decorates the request in onBeforeConnect", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        session_id: SESSION_ID,
        workspace_id: "workspace-1",
        room_id: `thinking-session:${SESSION_ID}`,
        can_view: true,
        can_participate: true,
        can_control: true,
        is_host: true,
        phase: "gathering",
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "user-1",
        email: "user@example.com",
        full_name: "User One",
      }), { status: 200 }));

    vi.stubGlobal("fetch", fetchMock);

    const request = {
      url: `https://collab.example.com/parties/main/thinking-session:${SESSION_ID}?token=test-token`,
      headers: new Headers(),
    } as unknown as Party.Request;

    const result = await ThinkingSessionServer.onBeforeConnect(request, {
      env: { PARTYKIT_BACKEND_URL: "http://backend.test" },
    } as unknown as Party.Lobby);

    expect(result).toBe(request);
    expect(request.headers.get("x-thinking-user-id")).toBe("user-1");
    expect(request.headers.get("x-thinking-can-control")).toBe("1");
    expect(request.headers.get("x-thinking-auth-token")).toBe("test-token");
  });

  it("rejects onBeforeConnect when the user cannot view the session", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        session_id: SESSION_ID,
        workspace_id: "workspace-1",
        room_id: `thinking-session:${SESSION_ID}`,
        can_view: false,
        can_participate: false,
        can_control: false,
        is_host: false,
        phase: "waiting",
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "user-1",
        email: "user@example.com",
      }), { status: 200 }));

    vi.stubGlobal("fetch", fetchMock);

    const request = {
      url: `https://collab.example.com/parties/main/thinking-session:${SESSION_ID}?token=test-token`,
      headers: new Headers(),
    } as unknown as Party.Request;

    const result = await ThinkingSessionServer.onBeforeConnect(request, {
      env: { PARTYKIT_BACKEND_URL: "http://backend.test" },
    } as unknown as Party.Lobby);

    expect(result).toBeInstanceOf(Response);
    expect((result as Response).status).toBe(401);
  });

  it("rejects malformed thinking session room identifiers before backend access checks", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const request = {
      url: "https://collab.example.com/parties/main/thinking-session:not-a-uuid?token=test-token",
      headers: new Headers(),
    } as unknown as Party.Request;

    const result = await ThinkingSessionServer.onBeforeConnect(request, {
      env: { PARTYKIT_BACKEND_URL: "http://backend.test" },
    } as unknown as Party.Lobby);

    expect(result).toBeInstanceOf(Response);
    expect((result as Response).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends a server error for malformed messages", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(buildRawSession()), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const server = new ThinkingSessionServer(createRoom());
    const connection = new FakeConnection("conn-1");

    await server.onConnect(connection as unknown as Party.Connection, createContextForUser("user-1"));
    connection.sent.length = 0;

    await server.onMessage("{bad json", connection as unknown as Party.Connection);

    expect(parseLastSent(connection)).toMatchObject({
      type: "server_error",
      payload: { code: "invalid_message" },
    });
  });

  it("blocks participant actions when the connection is view-only", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(buildRawSession()), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const server = new ThinkingSessionServer(createRoom());
    const connection = new FakeConnection("conn-1");

    await server.onConnect(connection as unknown as Party.Connection, createContextForUser("user-1", {
      canParticipate: false,
    }));
    connection.sent.length = 0;

    await server.onMessage(JSON.stringify({
      type: "submit_contribution",
      payload: { content: "I should not be allowed" },
    }), connection as unknown as Party.Connection);

    expect(parseLastSent(connection)).toMatchObject({
      type: "server_error",
      payload: { code: "forbidden" },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("blocks control actions when the connection cannot control the session", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(buildRawSession()), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const server = new ThinkingSessionServer(createRoom());
    const connection = new FakeConnection("conn-1");

    await server.onConnect(connection as unknown as Party.Connection, createContextForUser("user-1"));
    connection.sent.length = 0;

    await server.onMessage(JSON.stringify({
      type: "transition_phase",
      payload: { targetPhase: "synthesizing" },
    }), connection as unknown as Party.Connection);

    expect(parseLastSent(connection)).toMatchObject({
      type: "server_error",
      payload: { code: "forbidden" },
    });

    await server.onMessage(JSON.stringify({
      type: "update_refinement",
      payload: { refinedOutput: "Not allowed" },
    }), connection as unknown as Party.Connection);

    expect(parseLastSent(connection)).toMatchObject({
      type: "server_error",
      payload: { code: "forbidden" },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rejects malformed action payloads without backend writes", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(buildRawSession()), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const server = new ThinkingSessionServer(createRoom());
    const connection = new FakeConnection("conn-1");

    await server.onConnect(connection as unknown as Party.Connection, createContextForUser("user-1", {
      canControl: true,
      isHost: true,
    }));
    connection.sent.length = 0;

    await server.onMessage(JSON.stringify({
      type: "toggle_vote",
      payload: { contributionId: "not-a-uuid" },
    }), connection as unknown as Party.Connection);

    expect(parseLastSent(connection)).toMatchObject({
      type: "server_error",
      payload: { code: "invalid_message" },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("broadcasts presence leave only after the last connection closes", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValue(new Response(JSON.stringify(buildRawSession()), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const room = createRoom();
    const server = new ThinkingSessionServer(room);
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

  it("starts the synthesis SSE stream only once for the same run", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(buildRawSession()), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        session: buildSynthesizingSession(),
        synthesis_run_id: RUN_ID,
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(
              `data: ${JSON.stringify({ type: "start", run_id: RUN_ID })}\n\n`,
            ));
          },
        }),
        { status: 200, headers: { "content-type": "text/event-stream" } },
      ))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        session: buildSynthesizingSession(),
        synthesis_run_id: RUN_ID,
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const room = createRoom();
    const server = new ThinkingSessionServer(room);
    const connection = new FakeConnection("conn-1");

    await server.onConnect(connection as unknown as Party.Connection, createContextForUser("user-1", {
      canControl: true,
      isHost: true,
    }));

    await server.onMessage(JSON.stringify({
      type: "transition_phase",
      payload: { targetPhase: "synthesizing" },
    }), connection as unknown as Party.Connection);

    await server.onMessage(JSON.stringify({
      type: "transition_phase",
      payload: { targetPhase: "synthesizing" },
    }), connection as unknown as Party.Connection);

    await flushPromises();

    const synthesisCalls = fetchMock.mock.calls.filter((call) => String(call[0]).includes("/synthesis/stream"));
    expect(synthesisCalls).toHaveLength(1);
  });

  it("delays synthesis stream retries after a failed stream", async () => {
    vi.useFakeTimers();

    const synthesizingSession = buildSynthesizingSession();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(buildRawSession()), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        session: synthesizingSession,
        synthesis_run_id: RUN_ID,
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response("stream unavailable", { status: 500 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(synthesizingSession), { status: 200 }))
      .mockResolvedValueOnce(new Response("stream unavailable", { status: 500 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(synthesizingSession), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const server = new ThinkingSessionServer(createRoom());
    const connection = new FakeConnection("conn-1");

    await server.onConnect(connection as unknown as Party.Connection, createContextForUser("user-1", {
      canControl: true,
      isHost: true,
    }));

    await server.onMessage(JSON.stringify({
      type: "transition_phase",
      payload: { targetPhase: "synthesizing" },
    }), connection as unknown as Party.Connection);

    await flushPromises();
    let synthesisCalls = fetchMock.mock.calls.filter((call) => String(call[0]).includes("/synthesis/stream"));
    expect(synthesisCalls).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(1_999);
    synthesisCalls = fetchMock.mock.calls.filter((call) => String(call[0]).includes("/synthesis/stream"));
    expect(synthesisCalls).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(1);
    await flushPromises();

    synthesisCalls = fetchMock.mock.calls.filter((call) => String(call[0]).includes("/synthesis/stream"));
    expect(synthesisCalls).toHaveLength(2);
  });
});
