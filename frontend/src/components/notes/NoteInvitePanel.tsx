import { useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import type { NoteInvite } from '@/types';

type NoteInvitePanelProps = {
  noteId: string;
  noteTitle: string;
  canManage: boolean;
};

const TT = {
  inkDeep: '#111111',
  inkRaised: '#1A1A1A',
  inkBorder: '#252525',
  inkMuted: '#888888',
  snow: '#F5F5F5',
  yolk: '#F5E642',
  error: '#FF4545',
  fontMono: "'IBM Plex Mono', monospace",
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontBody: "'IBM Plex Sans', sans-serif",
} as const;

function formatInviteStatus(invite: NoteInvite): string {
  if (invite.status === 'accepted' && invite.acceptedAt) {
    return `Accepted ${invite.acceptedAt.toLocaleString()}`;
  }

  if (invite.status === 'revoked' && invite.revokedAt) {
    return `Revoked ${invite.revokedAt.toLocaleString()}`;
  }

  if (invite.status === 'expired') {
    return 'Expired';
  }

  return `Expires ${invite.expiresAt.toLocaleString()}`;
}

export function NoteInvitePanel({ noteId, noteTitle, canManage }: NoteInvitePanelProps) {
  const [inviteeEmail, setInviteeEmail] = useState('');
  const [role, setRole] = useState<'viewer' | 'editor'>('editor');
  const [invites, setInvites] = useState<NoteInvite[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [latestInviteLink, setLatestInviteLink] = useState<string | null>(null);

  const pendingInvites = useMemo(
    () => invites.filter((invite) => invite.status === 'pending'),
    [invites],
  );

  const loadInvites = async () => {
    if (!canManage) {
      setInvites([]);
      return;
    }

    try {
      setIsLoading(true);
      setError(null);
      const nextInvites = await api.listNoteInvites(noteId);
      setInvites(nextInvites);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load invites';
      setError(message);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadInvites();
  }, [canManage, noteId]);

  const handleCreateInvite = async () => {
    if (!canManage || !inviteeEmail.trim()) {
      return;
    }

    try {
      setIsSubmitting(true);
      setError(null);
      setSuccessMessage(null);
      setLatestInviteLink(null);

      const response = await api.createNoteInvite(noteId, {
        invitee_email: inviteeEmail.trim(),
        role,
      });

      if (response.collaboratorUpdated) {
        setSuccessMessage(`Existing collaborator access was updated to ${response.collaboratorRole ?? role}.`);
      } else if (response.inviteToken) {
        const inviteLink = `${window.location.origin}/note-invites/accept?token=${encodeURIComponent(response.inviteToken)}`;
        setLatestInviteLink(inviteLink);
        setSuccessMessage(response.created ? 'Invite created.' : 'Existing invite refreshed.');
      } else {
        setSuccessMessage('Invite processed.');
      }

      setInviteeEmail('');
      await loadInvites();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create invite';
      setError(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRevokeInvite = async (inviteId: string) => {
    try {
      setError(null);
      await api.revokeNoteInvite(noteId, inviteId);
      setSuccessMessage('Invite revoked.');
      await loadInvites();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to revoke invite';
      setError(message);
    }
  };

  const handleCopyLink = async () => {
    if (!latestInviteLink) {
      return;
    }

    try {
      await navigator.clipboard.writeText(latestInviteLink);
      setSuccessMessage('Invite link copied.');
    } catch {
      setSuccessMessage('Invite link generated below.');
    }
  };

  if (!canManage) {
    return null;
  }

  return (
    <div
      style={{
        border: `1px solid ${TT.inkBorder}`,
        background: TT.inkDeep,
        borderRadius: 4,
        padding: 14,
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <span
          style={{
            fontFamily: TT.fontDisplay,
            fontSize: 20,
            letterSpacing: '0.08em',
            color: TT.snow,
            textTransform: 'uppercase',
          }}
        >
          Share Note
        </span>
        <span
          style={{
            fontFamily: TT.fontBody,
            fontSize: 12,
            lineHeight: 1.5,
            color: TT.inkMuted,
          }}
        >
          Invite collaborators to &quot;{noteTitle}&quot; with note-scoped access.
        </span>
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <input
          value={inviteeEmail}
          onChange={(event) => setInviteeEmail(event.target.value)}
          placeholder="teammate@example.com"
          style={{
            flex: '1 1 220px',
            minWidth: 0,
            height: 40,
            background: TT.inkRaised,
            border: `1px solid ${TT.inkBorder}`,
            borderRadius: 3,
            color: TT.snow,
            fontFamily: TT.fontMono,
            fontSize: 12,
            padding: '0 12px',
          }}
        />
        <select
          value={role}
          onChange={(event) => setRole(event.target.value as 'viewer' | 'editor')}
          style={{
            width: 110,
            height: 40,
            background: TT.inkRaised,
            border: `1px solid ${TT.inkBorder}`,
            borderRadius: 3,
            color: TT.snow,
            fontFamily: TT.fontMono,
            fontSize: 12,
            padding: '0 10px',
          }}
        >
          <option value="viewer">Viewer</option>
          <option value="editor">Editor</option>
        </select>
        <button
          onClick={() => void handleCreateInvite()}
          disabled={isSubmitting || !inviteeEmail.trim()}
          style={{
            height: 40,
            padding: '0 16px',
            borderRadius: 3,
            border: `2px solid ${TT.yolk}`,
            background: isSubmitting || !inviteeEmail.trim() ? TT.inkRaised : TT.yolk,
            color: isSubmitting || !inviteeEmail.trim() ? TT.inkMuted : '#0A0A0A',
            fontFamily: TT.fontDisplay,
            fontSize: 15,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            cursor: isSubmitting || !inviteeEmail.trim() ? 'not-allowed' : 'pointer',
          }}
        >
          Invite
        </button>
      </div>

      {latestInviteLink && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            padding: 10,
            borderRadius: 3,
            border: `1px solid rgba(245,230,66,0.2)`,
            background: 'rgba(245,230,66,0.05)',
          }}
        >
          <span style={{ fontFamily: TT.fontMono, fontSize: 11, color: TT.inkMuted }}>
            Invite link
          </span>
          <code
            style={{
              fontFamily: TT.fontMono,
              fontSize: 11,
              color: TT.snow,
              wordBreak: 'break-all',
            }}
          >
            {latestInviteLink}
          </code>
          <button
            onClick={() => void handleCopyLink()}
            style={{
              alignSelf: 'flex-start',
              height: 34,
              padding: '0 12px',
              borderRadius: 3,
              border: `1px solid ${TT.inkBorder}`,
              background: 'transparent',
              color: TT.snow,
              fontFamily: TT.fontMono,
              fontSize: 11,
              cursor: 'pointer',
            }}
          >
            Copy link
          </button>
        </div>
      )}

      {error && (
        <div style={{ color: TT.error, fontFamily: TT.fontMono, fontSize: 11 }}>
          {error}
        </div>
      )}

      {successMessage && !error && (
        <div style={{ color: TT.yolk, fontFamily: TT.fontMono, fontSize: 11 }}>
          {successMessage}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontFamily: TT.fontMono, fontSize: 11, color: TT.inkMuted }}>
            Pending invites ({pendingInvites.length})
          </span>
          {isLoading && (
            <span style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.inkMuted }}>
              Refreshing…
            </span>
          )}
        </div>

        {invites.length === 0 ? (
          <div style={{ fontFamily: TT.fontBody, fontSize: 12, color: TT.inkMuted }}>
            No invites yet.
          </div>
        ) : (
          invites.map((invite) => (
            <div
              key={invite.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 10,
                padding: '10px 12px',
                borderRadius: 3,
                border: `1px solid ${TT.inkBorder}`,
                background: TT.inkRaised,
                flexWrap: 'wrap',
              }}
            >
              <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ fontFamily: TT.fontMono, fontSize: 12, color: TT.snow }}>
                  {invite.inviteeEmail || invite.inviteeUserId || 'Unknown invitee'}
                </span>
                <span style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.inkMuted }}>
                  {invite.role.toUpperCase()} · {formatInviteStatus(invite)}
                </span>
              </div>
              {invite.status === 'pending' && (
                <button
                  onClick={() => void handleRevokeInvite(invite.id)}
                  style={{
                    height: 32,
                    padding: '0 10px',
                    borderRadius: 3,
                    border: `1px solid rgba(255,69,69,0.25)`,
                    background: 'transparent',
                    color: TT.error,
                    fontFamily: TT.fontMono,
                    fontSize: 11,
                    cursor: 'pointer',
                  }}
                >
                  Revoke
                </button>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
