import { Briefcase } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';

interface WorkspaceSwitcherProps {
  compact?: boolean;
}

const TT = {
  inkBorder: '#252525',
  inkMuted: '#5A5A5A',
  inkSubtle: '#888888',
  snow: '#F5F5F5',
  yolk: '#F5E642',
  inkDeep: '#111111',
  inkRaised: '#1A1A1A',
  fontMono: "'IBM Plex Mono', monospace",
};

export function WorkspaceSwitcher({ compact = false }: WorkspaceSwitcherProps) {
  const { workspaces, currentWorkspaceId, setCurrentWorkspace } = useAuth();

  if (!workspaces.length) {
    return null;
  }

  const resolvedWorkspaceId =
    workspaces.find((workspace) => workspace.id === currentWorkspaceId)?.id ||
    workspaces[0]?.id ||
    '';

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        minWidth: compact ? 0 : 220,
      }}
    >
      <div
        style={{
          width: compact ? 28 : 30,
          height: compact ? 28 : 30,
          borderRadius: 3,
          background: 'rgba(245,230,66,0.08)',
          border: '1px solid rgba(245,230,66,0.18)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
        }}
      >
        <Briefcase size={compact ? 13 : 14} color={TT.yolk} />
      </div>

      <div style={{ minWidth: 0, flex: 1 }}>
        {!compact && (
          <div
            style={{
              fontFamily: TT.fontMono,
              fontSize: 9,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              color: TT.inkSubtle,
              marginBottom: 4,
            }}
          >
            Workspace
          </div>
        )}

        <select
          value={resolvedWorkspaceId}
          onChange={(event) => setCurrentWorkspace(event.target.value)}
          aria-label="Switch workspace"
          style={{
            width: compact ? 170 : '100%',
            maxWidth: '100%',
            height: compact ? 30 : 34,
            background: compact ? TT.inkDeep : TT.inkRaised,
            border: `1px solid ${TT.inkBorder}`,
            borderRadius: 3,
            color: TT.snow,
            fontFamily: TT.fontMono,
            fontSize: compact ? 10.5 : 11,
            letterSpacing: '0.04em',
            padding: compact ? '0 28px 0 10px' : '0 32px 0 10px',
            outline: 'none',
            cursor: 'pointer',
          }}
        >
          {workspaces.map((workspace) => (
            <option key={workspace.id} value={workspace.id} style={{ background: TT.inkDeep, color: TT.snow }}>
              {compact ? workspace.name : `${workspace.name} (${workspace.role})`}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
