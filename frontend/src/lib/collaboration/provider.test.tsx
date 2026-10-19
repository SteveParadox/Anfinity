import { useEffect } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useNoteCollaborationSession, type NoteCollaborationSession } from "./provider";

const state = vi.hoisted(() => ({ ticket: vi.fn(), providers: [] as Array<{ connections: number; destroy: ReturnType<typeof vi.fn> }> }));
vi.mock("../api", () => ({ api: { createRealtimeTicket: state.ticket } }));
vi.mock("./localPersistence", () => ({ LocalDocumentPersistence: class { ready = Promise.resolve(); destroy() {} } }));
vi.mock("y-partyserver/provider", () => ({ default: class {
  awareness = { setLocalState: vi.fn() }; ws = null; connections = 0; wsUnsuccessfulReconnects = 0; shouldConnect = false;
  destroy = vi.fn(); disconnect() {} on() {} off() {} emit() {}
  constructor(_host: string, _room: string, _doc: unknown, private options: { params: () => Promise<unknown> }) { state.providers.push(this); }
  async connect() { await this.options.params(); this.connections++; }
} }));
let latest: NoteCollaborationSession;
function Harness({ token }: { token: string }) {
  const session = useNoteCollaborationSession({ userId: "user", noteId: "note", token });
  useEffect(() => { latest = session; }, [session]);
  return null;
}
afterEach(() => { state.providers.length = 0; vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("collaboration effect lifecycle", () => {
  it("cannot open a socket when an old ticket resolves after unmount", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    let finish!: (value: { ticket: string }) => void;
    state.ticket.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    let view!: ReactTestRenderer;
    await act(async () => { view = create(<Harness token="token" />); });
    await act(async () => { view.unmount(); });
    await act(async () => { finish({ ticket: "late-ticket" }); });
    expect(state.providers[0].connections).toBe(0);
  });
  it("retains unsent CRDT edits when the same user's credentials rotate", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    state.ticket.mockResolvedValue({ ticket: "ticket" });
    let view!: ReactTestRenderer;
    await act(async () => { view = create(<Harness token="first" />); });
    const doc = latest.doc!; doc.getText("pending").insert(0, "unsent");
    await act(async () => { view.update(<Harness token="second" />); });
    expect(latest.doc).toBe(doc);
    expect(latest.doc!.getText("pending").toString()).toBe("unsent");
    await act(async () => { view.unmount(); });
  });
});


it("shows configuration failure without crashing the note view", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubEnv("VITE_REALTIME_HOST", "http://invalid.example/path");
  let view!: ReactTestRenderer;
  await act(async () => { view = create(<Harness token="token" />); });
  expect(state.providers).toHaveLength(0);
  expect(latest.lastError).toContain("configuration");
  await act(async () => { view.unmount(); });
});
