import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { api } from "../api";
import YPartyServerProvider from "y-partyserver/provider";
import * as Y from "yjs";
import { LocalDocumentPersistence } from "./localPersistence";
import { connectInitially } from "./initialConnection";
import type {
  CollaborationClientMessage,
  CollaborationServerMessage,
  CollaboratorPresence,
  CursorState,
  SelectionState,
} from "./protocol";
import { parseCollaborationServerMessage } from "./protocol";
import { getRealtimeConnection, getNoteRoomId } from "./noteRoom";

class BoundedYProvider extends YPartyServerProvider {
  async _reconnectWS(): Promise<void> {
    if (this.wsUnsuccessfulReconnects >= 12) {
      this.disconnect();
      this.emit("connection-error", [new Error("Collaboration retries exhausted")]);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, Math.random() * 250));
    if (this.shouldConnect) await super._reconnectWS();
  }
}

type ProviderStatus = "idle" | "connecting" | "connected" | "disconnected";

export type NoteCollaborationSessionOptions = {
  userId?: string;
  noteId?: string | null;
  token?: string | null;
  enabled?: boolean;
};

export type NoteCollaborationSession = {
  doc: Y.Doc | null;
  provider: YPartyServerProvider | null;
  status: ProviderStatus;
  isSynced: boolean;
  collaborators: CollaboratorPresence[];
  typingByUserId: Record<string, boolean>;
  remoteCursors: Record<string, CursorState>;
  remoteSelections: Record<string, SelectionState>;
  lastError: string | null;
  recoveryWarning: string | null;
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
  userId,
  noteId,
  token,
  enabled = true,
}: NoteCollaborationSessionOptions): NoteCollaborationSession {
  const [doc, setDoc] = useState<Y.Doc | null>(null);
  const [provider, setProvider] = useState<YPartyServerProvider | null>(null);
  const [status, setStatus] = useState<ProviderStatus>("idle");
  const [isSynced, setIsSynced] = useState(false);
  const [collaborators, setCollaborators] = useState<CollaboratorPresence[]>([]);
  const [typingByUserId, setTypingByUserId] = useState<Record<string, boolean>>({});
  const [remoteCursors, setRemoteCursors] = useState<Record<string, CursorState>>({});
  const [remoteSelections, setRemoteSelections] = useState<Record<string, SelectionState>>({});
  const [lastError, setLastError] = useState<string | null>(null);
  const [recoveryWarning, setRecoveryWarning] = useState<string | null>(null);

  const providerRef = useRef<YPartyServerProvider | null>(null);
  const documentRef = useRef<{ noteId: string; userId?: string; doc: Y.Doc } | null>(null);
  useEffect(() => () => { documentRef.current?.doc.destroy(); documentRef.current = null; }, []);
  const resetTransientState = useCallback(() => {
    const next = createEmptyTransientState();
    setCollaborators(next.collaborators);
    setTypingByUserId(next.typingByUserId);
    setRemoteCursors(next.remoteCursors);
    setRemoteSelections(next.remoteSelections);
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

  const sendCollaborationMessage = useCallback((message: CollaborationClientMessage) => {
    const currentProvider = providerRef.current;
    const socket = currentProvider?.ws;

    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }

    currentProvider.sendMessage(JSON.stringify(message));
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
      setRecoveryWarning(null);
      return;
    }

    if (documentRef.current && (documentRef.current.noteId !== trimmedNoteId || documentRef.current.userId !== userId)) documentRef.current.doc.destroy();
    const nextDoc = documentRef.current?.noteId === trimmedNoteId && documentRef.current.userId === userId ? documentRef.current.doc : new Y.Doc();
    documentRef.current = { noteId: trimmedNoteId, userId, doc: nextDoc };
    let isDisposed = false;
    const abort = new AbortController();
    let connection: ReturnType<typeof getRealtimeConnection>;
    try { connection = getRealtimeConnection(); } catch {
      setLastError("Real-time service configuration is missing or invalid. Contact your workspace administrator.");
      setStatus("disconnected"); setIsSynced(false); setProvider(null); setDoc(nextDoc);
      return;
    }
    const nextProvider = new BoundedYProvider(
      connection.host,
      getNoteRoomId(trimmedNoteId),
      nextDoc,
      {
        connect: false,
        protocol: connection.protocol,
        disableBc: true,
        params: async () => {
          if (isDisposed) throw new DOMException("Connection disposed", "AbortError");
          try {
            const { ticket } = await api.createRealtimeTicket(getNoteRoomId(trimmedNoteId));
            if (isDisposed) throw new DOMException("Connection disposed", "AbortError");
            return { ticket };
          } catch (error) {
            const status = (error as { status?: number }).status;
            if (status === 401 || status === 403 || status === 404) nextProvider.disconnect();
            throw error;
          }
        },
      },
    );

    setRecoveryWarning(null);
    const persistence = userId ? new LocalDocumentPersistence(`anfinity:${userId}:note:${trimmedNoteId}`, nextDoc, () => {
      if (!isDisposed) setRecoveryWarning("Local recovery is unavailable. Keep this note open until live sync completes.");
    }) : null;

    const handleStatus = (event: { status: ProviderStatus }) => {
      if (isDisposed) {
        return;
      }

      setStatus(event.status);

      if (event.status === "connected") {
        setLastError(null);
      }

      if (event.status === "disconnected") {
        setIsSynced(false);
        resetTransientState();
      }
    };

    const handleSynced = (synced: boolean) => {
      if (isDisposed) {
        return;
      }

      setIsSynced(synced);
    };

    const handleConnectionError = () => {
      if (isDisposed) {
        return;
      }

      setLastError("Collaboration connection failed");
      setIsSynced(false);
    };

    const handleConnectionClose = (event: CloseEvent) => {
      if (event?.code === 4401 || event?.code === 4403) {
        nextProvider.disconnect();
        setLastError("Access expired or revoked. Sign in and reopen this note.");
      }
      if (isDisposed) {
        return;
      }

      setIsSynced(false);
      resetTransientState();
    };

    const handleCustomMessage = (raw: string) => {
      const message = parseCollaborationServerMessage(raw);
      if (message) applyServerMessage(message);
    };
    nextProvider.on("custom-message", handleCustomMessage);
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

    void (persistence?.ready ?? Promise.resolve()).then(() => connectInitially(
      () => nextProvider.connect(), abort.signal,
      (error) => {
        if (!isDisposed) {
          const permanent = [401, 403, 404].includes((error as { status?: number }).status ?? 0);
          setLastError(permanent ? "Access expired or revoked. Reopen this note after signing in." : "Collaboration retries exhausted. Reopen this note to retry.");
          setStatus("disconnected");
        }
      },
    ));

    return () => {
      isDisposed = true;
      abort.abort();
      nextProvider.off("custom-message", handleCustomMessage);
      nextProvider.off("status", handleStatus);
      nextProvider.off("synced", handleSynced);
      nextProvider.off("connection-error", handleConnectionError);
      nextProvider.off("connection-close", handleConnectionClose);
      nextProvider.awareness.setLocalState(null);
      nextProvider.destroy();
      persistence?.destroy();
      // Keep unsent CRDT edits when credentials rotate for the same note.

      if (providerRef.current === nextProvider) {
        providerRef.current = null;
      }
    };
  }, [applyServerMessage, enabled, noteId, resetTransientState, token, userId]);

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
    recoveryWarning,
    sendCollaborationMessage,
    sendCursorMove,
    sendSelectionChange,
    setTypingIndicator,
  };
}
