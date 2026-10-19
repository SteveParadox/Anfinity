import * as Y from "yjs";

/** Reads the existing y-indexeddb update format, with bounded/handled failures. */
export class LocalDocumentPersistence {
  readonly ready: Promise<void>;
  private db: IDBDatabase | null = null;
  private disposed = false;
  private writes = 0;
  private warned = false;

  constructor(
    private readonly name: string,
    private readonly doc: Y.Doc,
    private readonly onUnavailable: () => void,
    factory: IDBFactory | undefined = globalThis.indexedDB,
    timeoutMs = 3000,
  ) {
    let deadline: ReturnType<typeof setTimeout>;
    this.ready = Promise.race([
      this.load(factory, timeoutMs),
      new Promise<void>((resolve) => { deadline = setTimeout(() => { this.warn(); this.destroy(); resolve(); }, timeoutMs); }),
    ]).catch(() => this.warn()).finally(() => clearTimeout(deadline));
  }

  private warn(): void {
    if (!this.disposed && !this.warned) { this.warned = true; this.onUnavailable(); }
  }

  private async load(factory: IDBFactory | undefined, timeoutMs: number): Promise<void> {
    if (!factory) { this.warn(); return; }
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = factory.open(this.name, 1);
      let expired = false;
      const timeout = setTimeout(() => { expired = true; reject(new Error("Local recovery timeout")); }, timeoutMs);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains("updates")) request.result.createObjectStore("updates", { autoIncrement: true });
      };
      request.onerror = () => { clearTimeout(timeout); reject(new Error("Local recovery unavailable")); };
      request.onsuccess = () => {
        clearTimeout(timeout);
        if (expired || this.disposed) { request.result.close(); reject(new Error("Local recovery cancelled")); }
        else resolve(request.result);
      };
    });
    if (this.disposed) { db.close(); return; }
    this.db = db;
    db.onversionchange = () => { this.destroy(); this.onUnavailable(); };
    try {
      const updates = await new Promise<Uint8Array[]>((resolve, reject) => {
        const tx = db.transaction("updates", "readonly");
        const request = tx.objectStore("updates").getAll();
        tx.oncomplete = () => resolve(request.result);
        tx.onabort = tx.onerror = () => reject(new Error("Local recovery read failed"));
      });
      if (this.disposed) return;
      // Validate the entire cache before applying it to the active document.
      const restored = new Y.Doc();
      try {
        for (const update of updates) Y.applyUpdate(restored, update);
        Y.applyUpdate(this.doc, Y.encodeStateAsUpdate(restored), this);
      } finally { restored.destroy(); }
      this.writes = updates.length;
      this.doc.on("update", this.write);
      this.doc.on("destroy", this.destroy);
      this.write(Y.encodeStateAsUpdate(this.doc), null);
    } catch (error) {
      db.close(); this.db = null;
      throw error;
    }
  }

  private readonly write = (update: Uint8Array, origin: unknown): void => {
    if (!this.db || this.disposed || origin === this) return;
    try {
      const tx = this.db.transaction("updates", "readwrite");
      const store = tx.objectStore("updates");
      if (++this.writes >= 100) {
        // Other tabs may have durable edits that this tab has not received yet.
        // Merge under the write transaction before compacting their update log.
        const existing = store.getAll();
        existing.onsuccess = () => {
          const merged = new Y.Doc();
          try {
            for (const saved of existing.result) Y.applyUpdate(merged, saved);
            Y.applyUpdate(merged, Y.encodeStateAsUpdate(this.doc));
            const snapshot = Y.encodeStateAsUpdate(merged);
            store.clear(); store.add(snapshot);
          } catch { tx.abort(); this.warn(); }
          finally { merged.destroy(); }
        };
        this.writes = 0;
      } else store.add(update);
      tx.onerror = tx.onabort = () => this.warn();
    } catch { this.warn(); }
  };

  readonly destroy = (): void => {
    this.disposed = true;
    this.doc.off("update", this.write);
    this.doc.off("destroy", this.destroy);
    // close() drains already-created write transactions before releasing the DB.
    this.db?.close(); this.db = null;
  };
}
