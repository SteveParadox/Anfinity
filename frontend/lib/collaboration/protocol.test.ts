import { describe, expect, it } from "vitest";
import {
  isCollaborationServerMessage,
  parseCollaborationServerMessage,
  type CollaborationServerMessage,
} from "./protocol";

const collaborator = {
  userId: "user-1",
  email: "user@example.com",
  name: "User One",
  color: "#ff8800",
  canUpdate: true,
};

const presence = {
  ...collaborator,
  connectedAt: new Date("2026-04-23T12:00:00.000Z").toISOString(),
  connectionCount: 1,
};

describe("collaboration protocol validation", () => {
  it("accepts well-formed server messages", () => {
    const messages: CollaborationServerMessage[] = [
      {
        type: "presence_snapshot",
        payload: {
          collaborators: [presence],
        },
      },
      {
        type: "presence_join",
        payload: {
          collaborator: presence,
        },
      },
      {
        type: "presence_leave",
        payload: {
          userId: collaborator.userId,
        },
      },
      {
        type: "cursor_move",
        payload: {
          collaborator,
          cursor: {
            position: 1,
            anchor: 1,
            head: 1,
            clientX: 120.5,
            clientY: null,
          },
        },
      },
      {
        type: "selection_change",
        payload: {
          collaborator,
          selection: {
            from: 1,
            to: 3,
            empty: false,
          },
        },
      },
      {
        type: "typing_indicator",
        payload: {
          collaborator,
          isTyping: true,
        },
      },
      {
        type: "server_error",
        payload: {
          code: "invalid_message",
          message: "Malformed collaboration payload",
        },
      },
    ];

    for (const message of messages) {
      expect(isCollaborationServerMessage(message)).toBe(true);
      expect(parseCollaborationServerMessage(JSON.stringify(message))).toEqual(message);
    }
  });

  it("rejects malformed JSON and unknown message types", () => {
    expect(parseCollaborationServerMessage("{")).toBeNull();
    expect(parseCollaborationServerMessage(JSON.stringify({
      type: "presence_ghost",
      payload: {},
    }))).toBeNull();
  });

  it("rejects invalid collaborator presence payloads", () => {
    expect(isCollaborationServerMessage({
      type: "presence_join",
      payload: {
        collaborator: {
          ...presence,
          connectionCount: 0,
        },
      },
    })).toBe(false);
  });

  it("rejects invalid cursor and selection payloads", () => {
    expect(isCollaborationServerMessage({
      type: "cursor_move",
      payload: {
        collaborator,
        cursor: {
          position: -1,
          anchor: null,
          head: null,
          clientX: null,
          clientY: null,
        },
      },
    })).toBe(false);

    expect(isCollaborationServerMessage({
      type: "selection_change",
      payload: {
        collaborator,
        selection: {
          from: 10,
          to: 2,
          empty: false,
        },
      },
    })).toBe(false);
  });
});
