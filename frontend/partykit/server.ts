import type * as Party from "partykit/server";
import NoteCollaborationServer from "./collaboration-server";
import ThinkingSessionServer from "./thinking-session-server";
import { isThinkingSessionRoomId } from "../src/lib/thinkingSessions/room";

type DelegatedServer = Party.Server & {
  constructor: {
    onBeforeConnect?: (
      request: Party.Request,
      lobby: Party.Lobby,
    ) => Promise<Party.Request | Response>;
  };
};

function resolveServerClass(roomId: string) {
  return isThinkingSessionRoomId(roomId)
    ? ThinkingSessionServer
    : NoteCollaborationServer;
}

function getRoomIdFromRequest(requestUrl: string): string {
  const url = new URL(requestUrl);
  return url.pathname.split("/").filter(Boolean).at(-1) ?? "";
}

export default class RootPartyServer implements Party.Server {
  private readonly delegate: DelegatedServer;

  constructor(readonly room: Party.Room) {
    const ServerClass = resolveServerClass(room.id);
    this.delegate = new ServerClass(room) as DelegatedServer;
  }

  static async onBeforeConnect(
    request: Party.Request,
    lobby: Party.Lobby,
  ): Promise<Party.Request | Response> {
    const roomId = getRoomIdFromRequest(request.url);
    const ServerClass = resolveServerClass(roomId);

    if (typeof ServerClass.onBeforeConnect === "function") {
      return ServerClass.onBeforeConnect(request, lobby);
    }

    return request;
  }

  readonly options = {
    hibernate: false,
  };

  async onConnect(connection: Party.Connection, ctx: Party.ConnectionContext): Promise<void> {
    if (typeof this.delegate.onConnect === "function") {
      await this.delegate.onConnect(connection, ctx);
    }
  }

  async onMessage(
    message: string | ArrayBuffer | ArrayBufferView,
    sender: Party.Connection,
  ): Promise<void> {
    if (typeof this.delegate.onMessage === "function") {
      await this.delegate.onMessage(message, sender);
    }
  }

  async onClose(connection: Party.Connection): Promise<void> {
    if (typeof this.delegate.onClose === "function") {
      await this.delegate.onClose(connection);
    }
  }

  async onError(connection: Party.Connection, error: Error): Promise<void> {
    if (typeof this.delegate.onError === "function") {
      await this.delegate.onError(connection, error);
    }
  }
}
