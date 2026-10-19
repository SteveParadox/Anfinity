import { afterEach, describe, expect, it, vi } from "vitest";
import type * as Party from "./test-types";
vi.mock("partyserver", () => import("./test-runtime"));
vi.mock("y-partyserver", () => import("./test-runtime"));
import NoteCollaborationServer from "./collaboration-server";
import ThinkingSessionServer from "./thinking-session-server";
import { parseRoomPath } from "./security";

const NOTE_ID = "11111111-1111-4111-8111-111111111111";
const SESSION_ID = "22222222-2222-4222-8222-222222222222";

function createLobby(): Party.Lobby {
  return {
    env: {
      PARTYKIT_BACKEND_URL: "http://backend.test",
    },
  } as unknown as Party.Lobby;
}

describe("RootPartyServer room routing", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("routes note rooms to the note collaboration access checks", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        note_id: NOTE_ID,
        access_source: "workspace",
        can_view: true,
        can_update: true,
        can_delete: false,
        can_manage: false,
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "user-1",
        email: "user@example.com",
      }), { status: 200 }));

    vi.stubGlobal("fetch", fetchMock);

    const request = {
      url: `https://collab.example.com/parties/main/note:${NOTE_ID}?token=test-token`,
      headers: new Headers(),
    } as unknown as Party.Request;

    const result = await NoteCollaborationServer.onBeforeConnect(request, createLobby());

    expect(result).toBe(request);
    expect(String(fetchMock.mock.calls[0][0])).toBe(`http://backend.test/notes/${NOTE_ID}/access`);
  });

  it("routes thinking session rooms to thinking session access checks", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        session_id: SESSION_ID,
        workspace_id: "workspace-1",
        room_id: `thinking-session:${SESSION_ID}`,
        can_view: true,
        can_participate: true,
        can_control: false,
        is_host: false,
        phase: "gathering",
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

    const result = await ThinkingSessionServer.onBeforeConnect(request, createLobby());

    expect(result).toBe(request);
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `http://backend.test/thinking-sessions/${SESSION_ID}/access`,
    );
  });
});


describe("strict room paths", () => {
  it("preserves main namespace and rejects unknown and malformed rooms", () => {
    expect(parseRoomPath(`/parties/main/note:${NOTE_ID}`)?.kind).toBe("note");
    expect(parseRoomPath("/parties/main/NOTE:AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA")?.room).toBe("note:aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
    expect(parseRoomPath(`/parties/main/thinking-session:${SESSION_ID}`)?.kind).toBe("thinking-session");
    for (const path of ["/parties/main/anything", "/parties/main/note:", `/parties/notes/note:${NOTE_ID}`, `/parties/main/note:${NOTE_ID}/extra`, "/parties/main/%XX"]) {
      expect(parseRoomPath(path)).toBeNull();
    }
  });
});
