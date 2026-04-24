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

const DOCUMENT_POSITION_LIMIT = 1_000_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isBoolean(value: unknown): value is boolean {
  return typeof value === "boolean";
}

function isNonNegativeIntegerOrNull(value: unknown): value is number | null {
  return value === null
    || (
      typeof value === "number"
      && Number.isInteger(value)
      && value >= 0
      && value <= DOCUMENT_POSITION_LIMIT
    );
}

function isFiniteNumberOrNull(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value));
}

function isCollaboratorIdentity(value: unknown): value is CollaboratorIdentity {
  return isRecord(value)
    && isString(value.userId)
    && isString(value.email)
    && isString(value.name)
    && isString(value.color)
    && isBoolean(value.canUpdate);
}

function isCollaboratorPresence(value: unknown): value is CollaboratorPresence {
  if (!isRecord(value) || !isCollaboratorIdentity(value)) {
    return false;
  }

  const record = value as Record<string, unknown>;
  return typeof record.connectionCount === "number"
    && Number.isInteger(record.connectionCount)
    && record.connectionCount > 0
    && isString(record.connectedAt);
}

function isCursorState(value: unknown): value is CursorState {
  return isRecord(value)
    && isNonNegativeIntegerOrNull(value.position)
    && isNonNegativeIntegerOrNull(value.anchor)
    && isNonNegativeIntegerOrNull(value.head)
    && isFiniteNumberOrNull(value.clientX)
    && isFiniteNumberOrNull(value.clientY);
}

function isSelectionState(value: unknown): value is SelectionState {
  if (!isRecord(value)
    || !isNonNegativeIntegerOrNull(value.from)
    || !isNonNegativeIntegerOrNull(value.to)
    || !isBoolean(value.empty)
  ) {
    return false;
  }

  return value.from === null || value.to === null || value.from <= value.to;
}

export function isCollaborationServerMessage(
  value: unknown,
): value is CollaborationServerMessage {
  if (!isRecord(value) || !isString(value.type) || !isRecord(value.payload)) {
    return false;
  }

  if (value.type === "presence_snapshot") {
    return Array.isArray(value.payload.collaborators)
      && value.payload.collaborators.every(isCollaboratorPresence);
  }

  if (value.type === "presence_join") {
    return isCollaboratorPresence(value.payload.collaborator);
  }

  if (value.type === "presence_leave") {
    return isString(value.payload.userId);
  }

  if (value.type === "cursor_move") {
    return isCollaboratorIdentity(value.payload.collaborator)
      && isCursorState(value.payload.cursor);
  }

  if (value.type === "selection_change") {
    return isCollaboratorIdentity(value.payload.collaborator)
      && isSelectionState(value.payload.selection);
  }

  if (value.type === "typing_indicator") {
    return isCollaboratorIdentity(value.payload.collaborator)
      && isBoolean(value.payload.isTyping);
  }

  if (value.type === "server_error") {
    return isString(value.payload.code) && isString(value.payload.message);
  }

  return false;
}

export function parseCollaborationServerMessage(
  rawMessage: string,
): CollaborationServerMessage | null {
  try {
    const parsed = JSON.parse(rawMessage) as unknown;
    return isCollaborationServerMessage(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function serializeCollaborationMessage(
  message: CollaborationServerMessage,
): string {
  return JSON.stringify(message);
}
