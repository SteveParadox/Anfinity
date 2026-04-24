import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { CollaborativeNoteEditor } from '@/components/notes/CollaborativeNoteEditor';
import { useAuth } from '@/contexts/AuthContext';
import { api } from '@/lib/api';
import { getCollaboratorColor } from '@/lib/collaboration/colors';
import type { Note, NoteAccess } from '@/types';

const TT = {
  inkBlack: '#0A0A0A',
  inkDeep: '#111111',
  inkRaised: '#1A1A1A',
  inkBorder: '#252525',
  inkMuted: '#888888',
  snow: '#F5F5F5',
  yolk: '#F5E642',
  error: '#FF4545',
  success: '#34D399',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono: "'IBM Plex Mono', monospace",
  fontBody: "'IBM Plex Sans', sans-serif",
} as const;

export function SharedNotePage() {
  const { noteId } = useParams<{ noteId: string }>();
  const { user } = useAuth();
  const [note, setNote] = useState<Note | null>(null);
  const [access, setAccess] = useState<NoteAccess | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncState, setSyncState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const lastPersistedContentRef = useRef<string>('');

  useEffect(() => {
    let isCancelled = false;

    const load = async () => {
      if (!noteId) {
        setError('Missing note id');
        setIsLoading(false);
        return;
      }

      try {
        setIsLoading(true);
        setError(null);
        const [nextNote, nextAccess] = await Promise.all([
          api.getNote(noteId),
          api.getNoteAccess(noteId),
        ]);
        if (isCancelled) {
          return;
        }

        setNote(nextNote);
        setAccess(nextAccess);
        lastPersistedContentRef.current = nextNote.content;
      } catch (err) {
        if (isCancelled) {
          return;
        }

        setError(err instanceof Error ? err.message : 'Failed to load note');
      } finally {
        if (!isCancelled) {
          setIsLoading(false);
        }
      }
    };

    void load();
    return () => {
      isCancelled = true;
    };
  }, [noteId]);

  useEffect(() => {
    if (!note || !access?.canUpdate) {
      return;
    }

    if (note.content === lastPersistedContentRef.current) {
      return;
    }

    const controller = new AbortController();
    const timeout = window.setTimeout(async () => {
      try {
        setSyncState('saving');
        const response = await api.syncCollaborativeNoteContent(note.id, note.content, {
          baseContent: lastPersistedContentRef.current,
          signal: controller.signal,
        });
        lastPersistedContentRef.current = response.content;
        setSyncState('saved');
      } catch (err) {
        if (controller.signal.aborted) {
          return;
        }

        console.error('Failed to sync collaborative note content:', err);
        setSyncState('error');
      }
    }, 1200);

    return () => {
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [access?.canUpdate, note]);

  useEffect(() => {
    if (syncState !== 'saved') {
      return;
    }

    const timeout = window.setTimeout(() => setSyncState('idle'), 1800);
    return () => window.clearTimeout(timeout);
  }, [syncState]);

  if (isLoading) {
    return (
      <div
        style={{
          minHeight: '100vh',
          background: TT.inkBlack,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: TT.inkMuted,
          fontFamily: TT.fontMono,
          fontSize: 11,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
        }}
      >
        Loading shared note
      </div>
    );
  }

  if (error || !note || !access) {
    return (
      <div
        style={{
          minHeight: '100vh',
          background: TT.inkBlack,
          color: TT.snow,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 24,
        }}
      >
        <div
          style={{
            width: '100%',
            maxWidth: 520,
            background: TT.inkDeep,
            border: `1px solid ${TT.inkBorder}`,
            borderTop: `3px solid ${TT.error}`,
            padding: 20,
            borderRadius: 4,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}
        >
          <span style={{ fontFamily: TT.fontDisplay, fontSize: 28, letterSpacing: '0.08em' }}>
            Note Unavailable
          </span>
          <span style={{ fontFamily: TT.fontBody, fontSize: 14, color: TT.inkMuted }}>
            {error || 'This note could not be loaded.'}
          </span>
          <Link
            to="/"
            style={{
              alignSelf: 'flex-start',
              color: TT.yolk,
              fontFamily: TT.fontMono,
              fontSize: 12,
              textDecoration: 'none',
            }}
          >
            Return to app
          </Link>
        </div>
      </div>
    );
  }

  const collaborationToken = api.getToken();
  const syncLabel =
    syncState === 'saving'
      ? 'Saving…'
      : syncState === 'saved'
        ? 'Saved'
        : syncState === 'error'
          ? 'Save failed'
          : access.canUpdate
            ? 'Live sync enabled'
            : 'Read-only access';

  return (
    <div
      style={{
        minHeight: '100vh',
        background: TT.inkBlack,
        color: TT.snow,
        padding: '32px 20px 48px',
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: 1120,
          margin: '0 auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 18,
        }}
      >
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
            padding: 18,
            background: TT.inkDeep,
            border: `1px solid ${TT.inkBorder}`,
            borderTop: `3px solid ${TT.yolk}`,
            borderRadius: 4,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
              <Link
                to="/"
                style={{
                  color: TT.inkMuted,
                  fontFamily: TT.fontMono,
                  fontSize: 11,
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                  textDecoration: 'none',
                }}
              >
                Back to app
              </Link>
              <h1
                style={{
                  margin: 0,
                  fontFamily: TT.fontDisplay,
                  fontSize: 'clamp(28px, 4vw, 40px)',
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                  color: TT.snow,
                }}
              >
                {note.title}
              </h1>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-end' }}>
              <span
                style={{
                  color: access.canUpdate ? TT.success : TT.inkMuted,
                  fontFamily: TT.fontMono,
                  fontSize: 11,
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                }}
              >
                {syncLabel}
              </span>
              <span
                style={{
                  color: TT.inkMuted,
                  fontFamily: TT.fontMono,
                  fontSize: 11,
                }}
              >
                {access.canUpdate ? 'Editor access' : 'Viewer access'}
              </span>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: getCollaboratorColor(user?.id || note.userId),
              }}
            />
            <span style={{ fontFamily: TT.fontMono, fontSize: 11, color: TT.inkMuted }}>
              Signed in as {user?.full_name || user?.email || 'Collaborator'}
            </span>
          </div>
        </div>

        <CollaborativeNoteEditor
          noteId={note.id}
          token={collaborationToken}
          user={{
            userId: user?.id || note.userId,
            email: user?.email || '',
            name: user?.full_name || user?.email || 'Collaborator',
            color: getCollaboratorColor(user?.id || note.userId),
            canUpdate: access.canUpdate,
          }}
          editable={access.canUpdate}
          onPlainTextChange={(content) => {
            setNote((current) => {
              if (!current || current.content === content) {
                return current;
              }

              return {
                ...current,
                content,
              };
            });
          }}
        />
      </div>
    </div>
  );
}
