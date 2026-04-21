import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '@/lib/api';
import type { NoteInvite } from '@/types';

const TT = {
  inkBlack: '#0A0A0A',
  inkDeep: '#111111',
  inkRaised: '#1A1A1A',
  inkBorder: '#252525',
  inkMuted: '#888888',
  snow: '#F5F5F5',
  yolk: '#F5E642',
  error: '#FF4545',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono: "'IBM Plex Mono', monospace",
  fontBody: "'IBM Plex Sans', sans-serif",
} as const;

function describeInviteStatus(invite: NoteInvite): string {
  if (invite.status === 'accepted') {
    return 'This invite has already been accepted.';
  }
  if (invite.status === 'revoked') {
    return 'This invite has been revoked.';
  }
  if (invite.status === 'expired') {
    return 'This invite has expired.';
  }
  return `This invite grants ${invite.role} access.`;
}

export function AcceptNoteInvitePage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = searchParams.get('token')?.trim() || '';
  const [invite, setInvite] = useState<NoteInvite | null>(null);
  const [noteTitle, setNoteTitle] = useState<string>('Shared note');
  const [canAccept, setCanAccept] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isAccepting, setIsAccepting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isCancelled = false;

    const load = async () => {
      if (!token) {
        setError('Missing invite token');
        setIsLoading(false);
        return;
      }

      try {
        setIsLoading(true);
        setError(null);
        const response = await api.resolveNoteInvite(token);
        if (isCancelled) {
          return;
        }

        setInvite(response.invite);
        setNoteTitle(response.noteTitle);
        setCanAccept(response.canAccept);
      } catch (err) {
        if (isCancelled) {
          return;
        }

        setError(err instanceof Error ? err.message : 'Failed to resolve invite');
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
  }, [token]);

  const handleAccept = async () => {
    if (!token) {
      return;
    }

    try {
      setIsAccepting(true);
      setError(null);
      const response = await api.acceptNoteInvite(token);
      navigate(`/shared-notes/${response.note.id}`, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to accept invite');
    } finally {
      setIsAccepting(false);
    }
  };

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
          maxWidth: 560,
          background: TT.inkDeep,
          border: `1px solid ${TT.inkBorder}`,
          borderTop: `3px solid ${TT.yolk}`,
          borderRadius: 4,
          padding: 24,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span
            style={{
              fontFamily: TT.fontMono,
              fontSize: 11,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              color: TT.inkMuted,
            }}
          >
            Collaboration Invite
          </span>
          <h1
            style={{
              margin: 0,
              fontFamily: TT.fontDisplay,
              fontSize: 34,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
            }}
          >
            {noteTitle}
          </h1>
        </div>

        {isLoading ? (
          <div style={{ fontFamily: TT.fontMono, fontSize: 12, color: TT.inkMuted }}>
            Resolving invite…
          </div>
        ) : invite ? (
          <>
            <div
              style={{
                padding: 14,
                borderRadius: 4,
                border: `1px solid ${TT.inkBorder}`,
                background: TT.inkRaised,
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
              }}
            >
              <span style={{ fontFamily: TT.fontBody, fontSize: 14, lineHeight: 1.6 }}>
                {describeInviteStatus(invite)}
              </span>
              <span style={{ fontFamily: TT.fontMono, fontSize: 11, color: TT.inkMuted }}>
                Target: {invite.inviteeEmail || invite.inviteeUserId || 'Restricted recipient'}
              </span>
              <span style={{ fontFamily: TT.fontMono, fontSize: 11, color: TT.inkMuted }}>
                Expires: {invite.expiresAt.toLocaleString()}
              </span>
            </div>

            {error && (
              <div style={{ color: TT.error, fontFamily: TT.fontMono, fontSize: 11 }}>
                {error}
              </div>
            )}

            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <button
                onClick={() => void handleAccept()}
                disabled={!canAccept || isAccepting}
                style={{
                  height: 42,
                  padding: '0 18px',
                  borderRadius: 3,
                  border: `2px solid ${TT.yolk}`,
                  background: !canAccept || isAccepting ? TT.inkRaised : TT.yolk,
                  color: !canAccept || isAccepting ? TT.inkMuted : TT.inkBlack,
                  fontFamily: TT.fontDisplay,
                  fontSize: 16,
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                  cursor: !canAccept || isAccepting ? 'not-allowed' : 'pointer',
                }}
              >
                {isAccepting ? 'Accepting' : 'Accept Invite'}
              </button>
              <Link
                to="/"
                style={{
                  height: 42,
                  padding: '0 18px',
                  borderRadius: 3,
                  border: `1px solid ${TT.inkBorder}`,
                  color: TT.snow,
                  textDecoration: 'none',
                  display: 'inline-flex',
                  alignItems: 'center',
                  fontFamily: TT.fontMono,
                  fontSize: 11,
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                }}
              >
                Return to app
              </Link>
            </div>
          </>
        ) : (
          <div style={{ color: TT.error, fontFamily: TT.fontMono, fontSize: 11 }}>
            {error || 'Invite not found.'}
          </div>
        )}
      </div>
    </div>
  );
}
