import * as Y from "yjs";
export class Server {
  name: string;
  env: Record<string, unknown>;
  ctx: { storage: { get: () => Promise<undefined>; put: () => Promise<void> } };
  constructor(public room: { id: string; env: Record<string, unknown>; broadcast: (message: string, without: string[]) => void }) {
    this.name = room.id;
    this.env = room.env;
    this.ctx = { storage: { get: async () => undefined, put: async () => {}, transaction: async (fn: (t: unknown) => unknown) => fn({ get: async () => undefined, put: async () => {}, delete: async () => {} }) } };
  }
  broadcast(message: string, without: string[] = []) { this.room.broadcast(message, without); }
  getConnection(id: string) { return { id }; }
}
export class YServer extends Server {
  document = new Y.Doc();
  onConnect() {}
  onClose() {}
  onMessage() {}
  sendCustomMessage(connection: { send: (message: string) => void }, message: string) { connection.send(message); }
  broadcastCustomMessage(message: string, exclude?: { id: string }) { this.broadcast(message, exclude ? [exclude.id] : []); }
}
