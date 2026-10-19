import { describe, expect, it, vi } from "vitest";
import { IDBFactory } from "fake-indexeddb";
import * as Y from "yjs";
import { LocalDocumentPersistence } from "./localPersistence";

describe("local CRDT recovery", () => {
  it("restores updates after a cache restart without duplicating concurrent in-memory content", async () => {
    const factory = new IDBFactory();
    const first = new Y.Doc();
    const cache = new LocalDocumentPersistence("note", first, vi.fn(), factory);
    await cache.ready;
    first.getText("text").insert(0, "saved"); cache.destroy();
    const second = new Y.Doc();
    second.getText("unsent").insert(0, "pending");
    const warning = vi.fn();
    const reloaded = new LocalDocumentPersistence("note", second, warning, factory);
    await reloaded.ready;
    expect(second.getText("text").toString()).toBe("saved");
    expect(second.getText("unsent").toString()).toBe("pending");
    expect(warning).not.toHaveBeenCalled(); reloaded.destroy();
  });
  it("fails safely when browser storage is blocked", async () => {
    const warning = vi.fn();
    const factory = { open: () => { throw new Error("Access denied"); } } as unknown as IDBFactory;
    const cache = new LocalDocumentPersistence("note", new Y.Doc(), warning, factory);
    await cache.ready; expect(warning).toHaveBeenCalledTimes(1); cache.destroy();
  });
  it("does not merge corrupt cache data into the active document", async () => {
    const factory = new IDBFactory();
    const db = await new Promise<IDBDatabase>((resolve) => {
      const request = factory.open("corrupt", 1);
      request.onupgradeneeded = () => request.result.createObjectStore("updates", { autoIncrement: true });
      request.onsuccess = () => resolve(request.result);
    });
    await new Promise<void>((resolve) => {
      const tx = db.transaction("updates", "readwrite"); tx.objectStore("updates").add(new Uint8Array([255])); tx.oncomplete = () => resolve();
    }); db.close();
    const doc = new Y.Doc(); doc.getText("text").insert(0, "valid");
    const warning = vi.fn(); const cache = new LocalDocumentPersistence("corrupt", doc, warning, factory);
    await cache.ready;
    expect(doc.getText("text").toString()).toBe("valid"); expect(warning).toHaveBeenCalledTimes(1); cache.destroy();
  });
});


it("bounds waiting for a blocked IndexedDB open", async () => {
  vi.useFakeTimers();
  const request = {};
  const warning = vi.fn();
  const cache = new LocalDocumentPersistence("blocked", new Y.Doc(), warning, { open: () => request } as unknown as IDBFactory, 10);
  await vi.advanceTimersByTimeAsync(11);
  await cache.ready;
  expect(warning).toHaveBeenCalledTimes(1);
  cache.destroy(); vi.useRealTimers();
});


it("does not compact away another tab's unsynchronized local edits", async () => {
  const factory = new IDBFactory();
  const first = new Y.Doc();
  const second = new Y.Doc();
  const one = new LocalDocumentPersistence("shared", first, vi.fn(), factory);
  await one.ready;
  const two = new LocalDocumentPersistence("shared", second, vi.fn(), factory);
  await two.ready;
  second.getText("other-tab").insert(0, "unsent");
  for (let i = 0; i < 110; i++) first.getText("text").insert(i, "a");
  one.destroy(); two.destroy();
  const recovered = new Y.Doc();
  const cache = new LocalDocumentPersistence("shared", recovered, vi.fn(), factory);
  await cache.ready;
  expect(recovered.getText("text").length).toBe(110);
  expect(recovered.getText("other-tab").toString()).toBe("unsent");
  cache.destroy();
});
