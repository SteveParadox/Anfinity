import { useEffect, useMemo, useRef } from "react";
import type { Editor } from "@tiptap/react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Collaboration from "@tiptap/extension-collaboration";
import CollaborationCursor from "@tiptap/extension-collaboration-cursor";
import type { CollaboratorIdentity } from "@/lib/collaboration/protocol";
import { useNoteCollaborationSession } from "@/lib/collaboration/provider";

type CollaborativeNoteEditorProps = {
  noteId: string;
  token: string | null;
  user: CollaboratorIdentity;
  editable: boolean;
  onPlainTextChange?: (content: string) => void;
};

function getEditorText(editor: Editor): string {
  return editor.getText({ blockSeparator: "\n" }).trimEnd();
}

function renderRemoteCursor(user: Record<string, unknown>): HTMLElement {
  const cursor = document.createElement("span");
  const label = document.createElement("div");
  const color = typeof user.color === "string" ? user.color : "#F5E642";
  const name = typeof user.name === "string" ? user.name : "Collaborator";

  cursor.className = "anfinity-collaboration-cursor";
  cursor.style.borderColor = color;

  label.className = "anfinity-collaboration-cursor__label";
  label.style.backgroundColor = color;
  label.textContent = name;

  cursor.append(label);
  return cursor;
}

function renderRemoteSelection(user: Record<string, unknown>) {
  const color = typeof user.color === "string" ? user.color : "#F5E642";

  return {
    class: "anfinity-collaboration-selection",
    style: `background-color: ${color}22;`,
  };
}

function syncLocalUserAwareness(
  provider: ReturnType<typeof useNoteCollaborationSession>["provider"],
  user: CollaboratorIdentity,
) {
  provider?.awareness.setLocalStateField("user", user);
}

function clearTransientPresence(session: ReturnType<typeof useNoteCollaborationSession>) {
  session.setTypingIndicator(false);
  session.sendCursorMove({
    position: null,
    anchor: null,
    head: null,
    clientX: null,
    clientY: null,
  });
  session.sendSelectionChange({
    from: null,
    to: null,
    empty: true,
  });
}

export function CollaborativeNoteEditor({
  noteId,
  token,
  user,
  editable,
  onPlainTextChange,
}: CollaborativeNoteEditorProps) {
  const onPlainTextChangeRef = useRef(onPlainTextChange);
  const session = useNoteCollaborationSession({
    noteId,
    token,
    enabled: Boolean(noteId && token),
  });
  const localUser = useMemo<CollaboratorIdentity>(() => ({
    userId: user.userId,
    email: user.email,
    name: user.name,
    color: user.color,
    canUpdate: user.canUpdate,
  }), [user.canUpdate, user.color, user.email, user.name, user.userId]);

  useEffect(() => {
    onPlainTextChangeRef.current = onPlainTextChange;
  }, [onPlainTextChange]);

  const statusLabel = useMemo(() => {
    if (session.lastError) {
      return "Collaboration unavailable";
    }

    if (session.status === "connected" && session.isSynced) {
      return editable ? "Live sync active" : "Live read-only";
    }

    if (session.status === "connecting") {
      return "Connecting room";
    }

    if (session.status === "disconnected") {
      return "Disconnected";
    }

    return "Preparing room";
  }, [editable, session.isSynced, session.lastError, session.status]);

  const remoteTypingUsers = useMemo(() => {
    return session.collaborators.filter((collaborator) => {
      return (
        collaborator.userId !== user.userId
        && Boolean(session.typingByUserId[collaborator.userId])
      );
    });
  }, [session.collaborators, session.typingByUserId, user.userId]);

  const editor = useEditor(
    {
      immediatelyRender: false,
      editable,
      extensions: [
        StarterKit.configure({
          history: false,
        }),
        ...(session.doc && session.provider
          ? [
              Collaboration.configure({
                document: session.doc,
              }),
              CollaborationCursor.configure({
                provider: session.provider,
                user: localUser,
                render: renderRemoteCursor,
                selectionRender: renderRemoteSelection,
              }),
            ]
          : []),
      ],
      editorProps: {
        attributes: {
          class: "anfinity-collaboration-editor",
        },
      },
      onCreate: ({ editor: nextEditor }) => {
        syncLocalUserAwareness(session.provider, localUser);
        onPlainTextChangeRef.current?.(getEditorText(nextEditor));
      },
      onUpdate: ({ editor: nextEditor, transaction }) => {
        if (!transaction.docChanged) {
          return;
        }

        onPlainTextChangeRef.current?.(getEditorText(nextEditor));
        session.setTypingIndicator(true);
      },
      onSelectionUpdate: ({ editor: nextEditor }) => {
        const selection = nextEditor.state.selection;
        let clientX: number | null = null;
        let clientY: number | null = null;

        try {
          const coords = nextEditor.view.coordsAtPos(selection.head);
          clientX = coords.left;
          clientY = coords.top;
        } catch {
          clientX = null;
          clientY = null;
        }

        session.sendCursorMove({
          position: selection.head,
          anchor: selection.anchor,
          head: selection.head,
          clientX,
          clientY,
        });
        session.sendSelectionChange({
          from: selection.from,
          to: selection.to,
          empty: selection.empty,
        });
      },
      onBlur: () => {
        clearTransientPresence(session);
      },
    },
    [
      editable,
      noteId,
      session.doc,
      session.provider,
      localUser.canUpdate,
      localUser.color,
      localUser.email,
      localUser.name,
      localUser.userId,
    ],
  );

  useEffect(() => {
    if (!editor) {
      return;
    }

    editor.setEditable(editable);
    syncLocalUserAwareness(session.provider, localUser);
  }, [editable, editor, localUser, session.provider]);

  useEffect(() => {
    return () => {
      clearTransientPresence(session);
      session.provider?.awareness.setLocalState(null);
    };
  }, [noteId, session.provider]);

  return (
    <div
      style={{
        background: "#1A1A1A",
        border: "1px solid #252525",
        borderRadius: 3,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 12,
          padding: "10px 12px",
          borderBottom: "1px solid #252525",
          background: "rgba(245,230,66,0.03)",
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              fontFamily: "'IBM Plex Mono', monospace",
              fontSize: 10,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              color: session.lastError ? "#FF4545" : "#888888",
            }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background:
                  session.status === "connected" && session.isSynced
                    ? "#34D399"
                    : session.lastError
                      ? "#FF4545"
                      : "#F5E642",
                boxShadow:
                  session.status === "connected" && session.isSynced
                    ? "0 0 8px rgba(52,211,153,0.5)"
                    : session.lastError
                      ? "0 0 8px rgba(255,69,69,0.4)"
                      : "0 0 8px rgba(245,230,66,0.45)",
              }}
            />
            <span>{statusLabel}</span>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            {session.collaborators.map((collaborator) => (
              <div
                key={collaborator.userId}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "4px 7px",
                  borderRadius: 999,
                  border: "1px solid #252525",
                  background: "rgba(255,255,255,0.02)",
                  maxWidth: "100%",
                }}
              >
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: collaborator.color,
                    flexShrink: 0,
                  }}
                />
                <span
                  style={{
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontSize: 10,
                    color: "#F5F5F5",
                    letterSpacing: "0.03em",
                  }}
                >
                  {collaborator.userId === user.userId ? "You" : collaborator.name}
                </span>
                {!collaborator.canUpdate && (
                  <span
                    style={{
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize: 9,
                      textTransform: "uppercase",
                      letterSpacing: "0.05em",
                      color: "#888888",
                    }}
                  >
                    View
                  </span>
                )}
                {collaborator.connectionCount > 1 && (
                  <span
                    style={{
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize: 9,
                      textTransform: "uppercase",
                      letterSpacing: "0.05em",
                      color: "#888888",
                    }}
                  >
                    ×{collaborator.connectionCount}
                  </span>
                )}
              </div>
            ))}
            {session.collaborators.length <= 1 && (
              <span
                style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontSize: 10,
                  color: "#888888",
                  letterSpacing: "0.04em",
                  textTransform: "uppercase",
                }}
              >
                Just you in this note
              </span>
            )}
          </div>

          <div
            style={{
              minHeight: 16,
              fontFamily: "'IBM Plex Mono', monospace",
              fontSize: 10,
              color: remoteTypingUsers.length > 0 ? "#F5E642" : "#5A5A5A",
              letterSpacing: "0.04em",
              textTransform: "uppercase",
            }}
          >
            {remoteTypingUsers.length > 0
              ? `${remoteTypingUsers.map((collaborator) => collaborator.name).join(", ")} typing`
              : "No active typing signals"}
          </div>
        </div>
        <span
          style={{
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: 10,
            letterSpacing: "0.06em",
            textTransform: "uppercase",
            color: editable ? "#F5E642" : "#888888",
          }}
        >
          {editable ? "Editable" : "Read only"}
        </span>
      </div>

      {editor ? (
        <EditorContent editor={editor} />
      ) : (
        <div
          style={{
            minHeight: 240,
            padding: "14px 16px",
            color: "#888888",
            fontFamily: "'IBM Plex Sans', sans-serif",
            fontSize: 13,
            lineHeight: 1.7,
          }}
        >
          Preparing collaborative editor…
        </div>
      )}
    </div>
  );
}
