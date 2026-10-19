import * as Y from "yjs";

const CHUNK_BYTES = 64 * 1024;
type Manifest = { chunks: number; projectionChunks: number };

/** Atomic manifest + binary chunks stay below Durable Object per-value limits. */
export async function saveSnapshot(storage: DurableObjectStorage, doc: Y.Doc, backendContent: string): Promise<void> {
  const update = Y.encodeStateAsUpdate(doc);
  const chunks = Math.ceil(update.byteLength / CHUNK_BYTES);
  const projection = new TextEncoder().encode(backendContent);
  const projectionChunks = Math.ceil(projection.length / CHUNK_BYTES);
  await storage.transaction(async (transaction) => {
    const previous = await transaction.get<Manifest>("document:v1");
    for (let index = 0; index < chunks; index++) {
      await transaction.put(`document:chunk:${index}`, update.slice(index * CHUNK_BYTES, (index + 1) * CHUNK_BYTES));
    }
    for (let index = chunks; index < (previous?.chunks ?? 0); index++) await transaction.delete(`document:chunk:${index}`);
    for (let index = 0; index < projectionChunks; index++) await transaction.put(`projection:chunk:${index}`, projection.slice(index * CHUNK_BYTES, (index + 1) * CHUNK_BYTES));
    for (let index = projectionChunks; index < (previous?.projectionChunks ?? 0); index++) await transaction.delete(`projection:chunk:${index}`);
    await transaction.put("document:v1", { chunks, projectionChunks });
  });
}

export async function loadSnapshot(storage: DurableObjectStorage, doc: Y.Doc): Promise<(Manifest & { backendContent: string }) | null> {
  const manifest = await storage.get<Manifest>("document:v1");
  if (!manifest) return null;
  const chunks: Uint8Array[] = [];
  let length = 0;
  for (let index = 0; index < manifest.chunks; index++) {
    const chunk = await storage.get<Uint8Array>(`document:chunk:${index}`);
    if (!chunk) throw new Error("Incomplete collaboration snapshot; restore from backup");
    chunks.push(chunk); length += chunk.length;
  }
  const update = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { update.set(chunk, offset); offset += chunk.length; }
  Y.applyUpdate(doc, update);
  const decoder = new TextDecoder();
  let backendContent = "";
  for (let index = 0; index < manifest.projectionChunks; index++) {
    const chunk = await storage.get<Uint8Array>(`projection:chunk:${index}`);
    if (!chunk) throw new Error("Incomplete backend projection snapshot");
    backendContent += decoder.decode(chunk, { stream: true });
  }
  backendContent += decoder.decode();
  return { ...manifest, backendContent };
}
