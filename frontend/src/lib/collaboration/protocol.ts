export type CollaboratorIdentity = {
  userId: string;
  email: string;
  name: string;
  color: string;
  canUpdate: boolean;
};

export type CollaboratorPresence = CollaboratorIdentity & {
  connectionCount: number;
  connectedAt: string;
};

export type CursorState = {
  position: number | null;
  anchor: number | null;
  head: number | null;
  clientX: number | null;
  clientY: number | null;
};

export type SelectionState = {
  from: number | null;
  to: number | null;
  empty: boolean;
};

export type CursorMoveClientMessage = {
  type: "cursor_move";
  payload: CursorState;
};

export type SelectionChangeClientMessage = {
  type: "selection_change";
  payload: SelectionState;
};

export type TypingIndicatorClientMessage = {
  type: "typing_indicator";
  payload: {
    isTyping: boolean;
  };
};

export type CollaborationClientMessage =
  | CursorMoveClientMessage
  | SelectionChangeClientMessage
  | TypingIndicatorClientMessage;

export type PresenceSnapshotServerMessage = {
  type: "presence_snapshot";
  payload: {
    collaborators: CollaboratorPresence[];
  };
};

export type PresenceJoinServerMessage = {
  type: "presence_join";
  payload: {
    collaborator: CollaboratorPresence;
  };
};

export type PresenceLeaveServerMessage = {
  type: "presence_leave";
  payload: {
    userId: string;
  };
};

export type CursorMoveServerMessage = {
  type: "cursor_move";
  payload: {
    collaborator: CollaboratorIdentity;
    cursor: CursorState;
  };
};

export type SelectionChangeServerMessage = {
  type: "selection_change";
  payload: {
    collaborator: CollaboratorIdentity;
    selection: SelectionState;
  };
};

export type TypingIndicatorServerMessage = {
  type: "typing_indicator";
  payload: {
    collaborator: CollaboratorIdentity;
    isTyping: boolean;
  };
};

export type CollaborationErrorServerMessage = {
  type: "server_error";
  payload: {
    code: string;
    message: string;
  };
};

export type CollaborationServerMessage =
  | PresenceSnapshotServerMessage
  | PresenceJoinServerMessage
  | PresenceLeaveServerMessage
  | CursorMoveServerMessage
  | SelectionChangeServerMessage
  | TypingIndicatorServerMessage
  | CollaborationErrorServerMessage;

export function serializeCollaborationMessage(
  message: CollaborationServerMessage,
): string {
  return JSON.stringify(message);
}

