import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import type PartySocket from "partysocket";
import YPartyKitProvider from "y-partykit/provider";
import * as Y from "yjs";
import type {
  CollaborationClientMessage,
  CollaborationServerMessage,
  CollaboratorPresence,
  CursorState,
  SelectionState,
} from "./protocol";
import { getPartykitHost, getNoteRoomId } from "./noteRoom";

type ProviderStatus = "idle" | "connecting" | "connected" | "disconnected";

export type NoteCollaborationSessionOptions = {
  noteId?: string | null;
  token?: string | null;
  enabled?: boolean;
};

export type NoteCollaborationSession = {
  doc: Y.Doc | null;
  provider: YPartyKitProvider | null;
  status: ProviderStatus;
  isSynced: boolean;
  collaborators: CollaboratorPresence[];
  typingByUserId: Record<string, boolean>;
  remoteCursors: Record<string, CursorState>;
  remoteSelections: Record<string, SelectionState>;
  lastError: string | null;
  sendCollaborationMessage: (message: CollaborationClientMessage) => void;
  sendCursorMove: (payload: CursorState) => void;
  sendSelectionChange: (payload: SelectionState) => void;
  setTypingIndicator: (isTyping: boolean) => void;
};

function createEmptyTransientState() {
  return {
    collaborators: [] as CollaboratorPresence[],
    typingByUserId: {} as Record<string, boolean>,
    remoteCursors: {} as Record<string, CursorState>,
    remoteSelections: {} as Record<string, SelectionState>,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseServerMessage(rawMessage: string): CollaborationServerMessage | null {
  try {
    const parsed = JSON.parse(rawMessage) as unknown;
    if (!isRecord(parsed) || typeof parsed.type !== "string" || !isRecord(parsed.payload)) {
      return null;
    }

    return parsed as CollaborationServerMessage;
  } catch {
    return null;
  }
}

function upsertCollaborator(
  collaborators: CollaboratorPresence[],
  collaborator: CollaboratorPresence,
): CollaboratorPresence[] {
  const next = collaborators.filter((entry) => entry.userId !== collaborator.userId);
  next.push(collaborator);
  next.sort((left, right) => left.name.localeCompare(right.name));
  return next;
}

export function useNoteCollaborationSession({
  noteId,
  token,
  enabled = true,
}: NoteCollaborationSessionOptions): NoteCollaborationSession {
  const [doc, setDoc] = useState<Y.Doc | null>(null);
  const [provider, setProvider] = useState<YPartyKitProvider | null>(null);
  const [status, setStatus] = useState<ProviderStatus>("idle");
  const [isSynced, setIsSynced] = useState(false);
  const [collaborators, setCollaborators] = useState<CollaboratorPresence[]>([]);
  const [typingByUserId, setTypingByUserId] = useState<Record<string, boolean>>({});
  const [remoteCursors, setRemoteCursors] = useState<Record<string, CursorState>>({});
  const [remoteSelections, setRemoteSelections] = useState<Record<string, SelectionState>>({});
  const [lastError, setLastError] = useState<string | null>(null);

  const providerRef = useRef<YPartyKitProvider | null>(null);
  const socketListenerRef = useRef<{
    socket: PartySocket;
    handler: (event: MessageEvent) => void;
  } | null>(null);

  const resetTransientState = useCallback(() => {
    const next = createEmptyTransientState();
    setCollaborators(next.collaborators);
    setTypingByUserId(next.typingByUserId);
    setRemoteCursors(next.remoteCursors);
    setRemoteSelections(next.remoteSelections);
  }, []);

  const detachSocketListener = useCallback(() => {
    const current = socketListenerRef.current;
    if (!current) {
      return;
    }

    current.socket.removeEventListener("message", current.handler);
    socketListenerRef.current = null;
  }, []);

  const applyServerMessage = useCallback((message: CollaborationServerMessage) => {
    if (message.type === "presence_snapshot") {
      setCollaborators(message.payload.collaborators);
      return;
    }

    if (message.type === "presence_join") {
      setCollaborators((current) => upsertCollaborator(current, message.payload.collaborator));
      return;
    }

    if (message.type === "presence_leave") {
      setCollaborators((current) =>
        current.filter((collaborator) => collaborator.userId !== message.payload.userId),
      );
      setTypingByUserId((current) => {
        const next = { ...current };
        delete next[message.payload.userId];
        return next;
      });
      setRemoteCursors((current) => {
        const next = { ...current };
        delete next[message.payload.userId];
        return next;
      });
      setRemoteSelections((current) => {
        const next = { ...current };
        delete next[message.payload.userId];
        return next;
      });
      return;
    }

    if (message.type === "typing_indicator") {
      setTypingByUserId((current) => {
        const next = { ...current };
        if (message.payload.isTyping) {
          next[message.payload.collaborator.userId] = true;
        } else {
          delete next[message.payload.collaborator.userId];
        }
        return next;
      });
      return;
    }

    if (message.type === "cursor_move") {
      setRemoteCursors((current) => ({
        ...current,
        [message.payload.collaborator.userId]: message.payload.cursor,
      }));
      return;
    }

    if (message.type === "selection_change") {
      setRemoteSelections((current) => ({
        ...current,
        [message.payload.collaborator.userId]: message.payload.selection,
      }));
      return;
    }

    if (message.type === "server_error") {
      setLastError(message.payload.message);
    }
  }, []);

  const attachSocketListener = useCallback((socket: PartySocket | null) => {
    detachSocketListener();

    if (!socket) {
      return;
    }

    const handler = (event: MessageEvent) => {
      if (typeof event.data !== "string") {
        return;
      }

      const message = parseServerMessage(event.data);
      if (!message) {
        return;
      }

      applyServerMessage(message);
    };

    socket.addEventListener("message", handler);
    socketListenerRef.current = {
      socket,
      handler,
    };
  }, [applyServerMessage, detachSocketListener]);

  const sendCollaborationMessage = useCallback((message: CollaborationClientMessage) => {
    const currentProvider = providerRef.current;
    const socket = currentProvider?.ws;

    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }

    socket.send(JSON.stringify(message));
  }, []);

  const sendCursorMove = useCallback((payload: CursorState) => {
    sendCollaborationMessage({
      type: "cursor_move",
      payload,
    });
  }, [sendCollaborationMessage]);

  const sendSelectionChange = useCallback((payload: SelectionState) => {
    sendCollaborationMessage({
      type: "selection_change",
      payload,
    });
  }, [sendCollaborationMessage]);

  const setTypingIndicator = useCallback((isTyping: boolean) => {
    sendCollaborationMessage({
      type: "typing_indicator",
      payload: {
        isTyping,
      },
    });
  }, [sendCollaborationMessage]);

  useEffect(() => {
    detachSocketListener();

    const trimmedNoteId = noteId?.trim();
    const trimmedToken = token?.trim();

    if (!enabled || !trimmedNoteId || !trimmedToken) {
      providerRef.current?.awareness.setLocalState(null);
      providerRef.current?.destroy();
      providerRef.current = null;
      setDoc(null);
      setProvider(null);
      setStatus("idle");
      setIsSynced(false);
      resetTransientState();
      setLastError(null);
      return;
    }

    const nextDoc = new Y.Doc();
    const nextProvider = new YPartyKitProvider(
      getPartykitHost(),
      getNoteRoomId(trimmedNoteId),
      nextDoc,
      {
        connect: false,
        params: async () => ({
          token: trimmedToken,
        }),
      },
    );

    let isDisposed = false;

    const handleStatus = (event: { status: ProviderStatus }) => {
      if (isDisposed) {
        return;
      }

      setStatus(event.status);

      if (event.status === "connected") {
        setLastError(null);
        attachSocketListener(nextProvider.ws as PartySocket | null);
      }

      if (event.status === "disconnected") {
        setIsSynced(false);
        detachSocketListener();
        resetTransientState();
      }
    };

    const handleSynced = (synced: boolean) => {
      if (isDisposed) {
        return;
      }

      setIsSynced(synced);
      if (synced) {
        attachSocketListener(nextProvider.ws as PartySocket | null);
      }
    };

    const handleConnectionError = () => {
      if (isDisposed) {
        return;
      }

      setLastError("Collaboration connection failed");
      setIsSynced(false);
    };

    const handleConnectionClose = () => {
      if (isDisposed) {
        return;
      }

      setIsSynced(false);
      detachSocketListener();
      resetTransientState();
    };

    nextProvider.on("status", handleStatus);
    nextProvider.on("synced", handleSynced);
    nextProvider.on("connection-error", handleConnectionError);
    nextProvider.on("connection-close", handleConnectionClose);

    providerRef.current = nextProvider;
    setDoc(nextDoc);
    setProvider(nextProvider);
    setStatus("connecting");
    setIsSynced(false);
    resetTransientState();
    setLastError(null);

    nextProvider.connect();

    return () => {
      isDisposed = true;
      detachSocketListener();
      nextProvider.off("status", handleStatus);
      nextProvider.off("synced", handleSynced);
      nextProvider.off("connection-error", handleConnectionError);
      nextProvider.off("connection-close", handleConnectionClose);
      nextProvider.awareness.setLocalState(null);
      nextProvider.destroy();
      nextDoc.destroy();

      if (providerRef.current === nextProvider) {
        providerRef.current = null;
      }
    };
  }, [attachSocketListener, detachSocketListener, enabled, noteId, resetTransientState, token]);

  return {
    doc,
    provider,
    status,
    isSynced,
    collaborators,
    typingByUserId,
    remoteCursors,
    remoteSelections,
    lastError,
    sendCollaborationMessage,
    sendCursorMove,
    sendSelectionChange,
    setTypingIndicator,
  };
}
