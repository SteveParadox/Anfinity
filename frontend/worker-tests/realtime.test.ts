import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { env, runInDurableObject } from "cloudflare:test";
import { loadSnapshot, saveSnapshot } from "../partykit/snapshot";
import worker from "../partykit/server";
import { getServerByName } from "partyserver";
import type { RealtimeEnv } from "../partykit/security";
import * as Y from "yjs";
import * as decoding from "lib0/decoding";
import * as encoding from "lib0/encoding";
import * as sync from "y-protocols/sync";

const bindings = env as unknown as RealtimeEnv;
const id = "11111111-1111-4111-8111-111111111111";
const origin = "http://localhost:5173";
function request(path = `/parties/main/note:${id}`, extra: Record<string, string> = {}) {
  return new Request(`http://worker.test${path}`, { headers: { Origin: origin, Upgrade: "websocket", ...extra } });
}
function backendAccess(canUpdate = true) {
  vi.stubGlobal("fetch", vi.fn(async (input: string | Request) => {
    const url = new URL(typeof input === "string" ? input : input.url);
    if (url.pathname === "/realtime/redeem") return Response.json({ token: "server-token" });
    if (url.pathname.endsWith("/access")) return Response.json({ can_view: true, can_update: canUpdate });
    if (url.pathname === "/auth/me") return Response.json({ id: "user-1", email: "user@example.com" });
    if (url.pathname === `/notes/${id}`) return Response.json({ id, content: "Existing note" });
    if (url.pathname.endsWith("/collaboration")) return Response.json({ id });
    throw new Error("Unexpected backend request");
  }));
}
function frame(doc: Y.Doc) {
  const encoder = encoding.createEncoder();
  encoding.writeVarUint(encoder, 0);
  sync.writeUpdate(encoder, Y.encodeStateAsUpdate(doc));
  return encoding.toUint8Array(encoder);
}
async function open(canUpdate = true) {
  backendAccess(canUpdate);
  const response = await worker.fetch(request(`/parties/main/note:${id}?ticket=one-use-ticket`), bindings);
  expect(response.status).toBe(101);
  const socket = response.webSocket!;
  socket.accept();
  return socket;
}

describe("Cloudflare real-time runtime", () => {
  beforeEach(() => { vi.unstubAllGlobals(); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("serves health, rejects unknown rooms, missing tickets and forbidden origins", async () => {
    expect((await worker.fetch(new Request("http://worker.test/health"), bindings)).status).toBe(200);
    expect((await worker.fetch(request("/parties/main/anything"), bindings)).status).toBe(404);
    expect((await worker.fetch(request(), bindings)).status).toBe(401);
    expect((await worker.fetch(request(undefined, { Origin: "https://evil.example" }), bindings)).status).toBe(403);
  });

  it("loads backend content, upgrades and persists Yjs updates in Durable Object storage", async () => {
    const socket = await open();
    const stub = await getServerByName(bindings.NOTES, `note:${id}`);
    await runInDurableObject(stub, async (instance, state) => {
      const doc = new Y.Doc();
      const saved = await loadSnapshot(state.storage, doc);
      expect(saved?.chunks).toBeGreaterThan(0);
      expect(doc.getXmlFragment("default").toString()).toContain("Existing note");
      // This check calls the actual lifecycle method with the real connection.
      const connection = [...instance.getConnections()].at(-1)!;
      const edit = new Y.Doc();
      edit.getText("test").insert(0, "durable edit");
      await instance.onMessage(connection, frame(edit));
      const restored = new Y.Doc();
      await loadSnapshot(state.storage, restored);
      expect(restored.getText("test").toString()).toBe("durable edit");
    });
    socket.close();
  });

  it("blocks handcrafted Yjs updates from viewers, including initial sync step 2", async () => {
    const socket = await open(false);
    const stub = await getServerByName(bindings.NOTES, `note:${id}`);
    await runInDurableObject(stub, async (instance) => {
      const connection = [...instance.getConnections()].at(-1)!;
      const edit = new Y.Doc();
      edit.getText("attack").insert(0, "unauthorized");
      await instance.onMessage(connection, frame(edit));
      const step2 = encoding.createEncoder();
      encoding.writeVarUint(step2, 0);
      sync.writeSyncStep2(step2, edit);
      await instance.onMessage(connection, encoding.toUint8Array(step2));
      expect(instance.document.getText("attack").toString()).toBe("");
    });
    socket.close();
  });
});


it("converges concurrent clients and restores committed state after eviction", async () => {
  backendAccess(true);
  const sockets: WebSocket[] = [];
  const docs = [new Y.Doc(), new Y.Doc()];
  for (const doc of docs) {
    const response = await worker.fetch(request(`/parties/main/note:${id}?ticket=concurrent-ticket&_pk=${crypto.randomUUID()}`), bindings);
    expect(response.status).toBe(101);
    const socket = response.webSocket!;
    socket.binaryType = "arraybuffer";
    socket.addEventListener("message", (event) => {
      if (typeof event.data === "string") return;
      const binary = event.data;
      const decoder = decoding.createDecoder(new Uint8Array(binary as ArrayBuffer));
      if (decoding.readVarUint(decoder) !== 0) return;
      const encoder = encoding.createEncoder();
      encoding.writeVarUint(encoder, 0);
      sync.readSyncMessage(decoder, encoder, doc, "remote");
      if (encoding.length(encoder) > 1) socket.send(encoding.toUint8Array(encoder));
    });
    doc.on("update", (update: Uint8Array, updateOrigin: unknown) => {
      if (updateOrigin === "remote") return;
      const encoder = encoding.createEncoder();
      encoding.writeVarUint(encoder, 0);
      sync.writeUpdate(encoder, update);
      socket.send(encoding.toUint8Array(encoder));
    });
    socket.accept();
    const initial = encoding.createEncoder();
    encoding.writeVarUint(initial, 0);
    sync.writeSyncStep1(initial, doc);
    socket.send(encoding.toUint8Array(initial));
    sockets.push(socket);
  }
  await vi.waitFor(() => expect(docs[1].getXmlFragment("default").toString()).toContain("Existing note"));
  docs[0].getText("concurrent").insert(0, "A");
  docs[1].getText("concurrent").insert(0, "B");
  await vi.waitFor(() => {
    expect(docs[0].getText("concurrent").length).toBe(2);
    expect(docs[1].getText("concurrent").toString()).toBe(docs[0].getText("concurrent").toString());
  });
  const expected = docs[0].getText("concurrent").toString();
  const stub = await getServerByName(bindings.NOTES, `note:${id}`);
  for (const socket of sockets) socket.close();
  await runInDurableObject(stub, async (instance) => {
    for (const connection of instance.getConnections()) {
      await instance.onClose(connection);
      connection.close();
    }
  });
  await expect(runInDurableObject(stub, (_instance, state) => { state.abort("test eviction"); })).rejects.toThrow();
  const restarted = await getServerByName(bindings.NOTES, `note:${id}`);
  await runInDurableObject(restarted, async (instance) => {
    expect(instance.document.getText("concurrent").toString()).toBe(expected);
  });
  for (const doc of docs) doc.destroy();
  vi.unstubAllGlobals();
});

it("routes thinking sessions separately and enforces control permissions in the runtime", async () => {
  const session = {
    id, workspace_id: "workspace", room_id: `thinking-session:${id}`, title: "Runtime thinking session",
    phase: "gathering", participants: [], contributions: [], synthesis_runs: [],
    synthesis_output: "", refined_output: "", final_output: "", created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
  };
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | Request) => {
    const path = new URL(typeof input === "string" ? input : input.url).pathname;
    requests.push(path);
    if (path === "/realtime/redeem") return Response.json({ token: "participant-token" });
    if (path === "/auth/me") return Response.json({ id: "participant", email: "participant@example.com" });
    if (path.endsWith("/access")) return Response.json({ can_view: true, can_participate: true, can_control: false, is_host: false });
    if (path.endsWith("/participants/ping")) return Response.json(session);
    throw new Error("Unexpected backend mutation");
  }));
  const response = await worker.fetch(request(`/parties/main/thinking-session:${id}?ticket=thinking-ticket`), bindings);
  expect(response.status).toBe(101);
  const socket = response.webSocket!;
  const messages: string[] = [];
  socket.addEventListener("message", (event) => { if (typeof event.data === "string") messages.push(event.data); });
  socket.accept();
  socket.send(JSON.stringify({ type: "transition_phase", payload: { targetPhase: "completed" } }));
  await vi.waitFor(() => expect(messages.some((message) => message.includes('"code":"forbidden"'))).toBe(true));
  expect(requests.some((path) => path.startsWith("/notes/"))).toBe(false);
  expect(requests.some((path) => path.endsWith("/transitions"))).toBe(false);
  socket.close();
  vi.unstubAllGlobals();
});


it("atomically stores large binary documents and projections in bounded chunks", async () => {
  const stub = await getServerByName(bindings.NOTES, "note:22222222-2222-4222-8222-222222222222");
  await runInDurableObject(stub, async (_instance, state) => {
    const doc = new Y.Doc();
    doc.getText("bulk").insert(0, "x".repeat(200000));
    await saveSnapshot(state.storage, doc, "text".repeat(50000));
    const restored = new Y.Doc();
    const manifest = await loadSnapshot(state.storage, restored);
    expect(manifest!.chunks).toBeGreaterThan(1);
    expect(manifest!.backendContent.length).toBe(200000);
    expect(restored.getText("bulk").length).toBe(200000);
    await saveSnapshot(state.storage, new Y.Doc(), "");
    expect(await state.storage.get("document:chunk:1")).toBeUndefined();
    expect(await state.storage.get("projection:chunk:1")).toBeUndefined();
  });
});
