import { useState, useEffect, useContext } from 'react';
import { motion } from 'framer-motion';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import {
  Users, Plus, Edit2, Trash2, UserPlus,
  Crown, Shield, User, FolderOpen, FileText, Clock, Eye,
} from 'lucide-react';
import type { Workspace, User as UserType } from '@/types';
import { formatDistanceToNow } from 'date-fns';
import { api } from '@/lib/api';
import { AuthContext } from '@/contexts/AuthContext';

interface WorkspacesViewProps {
  user: UserType;
}

const TT = {
  inkBlack:  '#0A0A0A',
  inkDeep:   '#111111',
  inkRaised: '#1A1A1A',
  inkBorder: '#252525',
  inkMid:    '#3A3A3A',
  inkMuted:  '#5A5A5A',
  inkSubtle: '#888888',
  snow:      '#F5F5F5',
  yolk:      '#F5E642',
  yolkBright:'#FFF176',
  error:     '#FF4545',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono:    "'IBM Plex Mono', monospace",
  fontBody:    "'IBM Plex Sans', sans-serif",
};

const roleConfig = {
  OWNER:  { Icon: Crown,  color: TT.yolk,    bg: 'rgba(245,230,66,0.08)',  border: 'rgba(245,230,66,0.2)'  },
  ADMIN:  { Icon: Shield, color: '#60A5FA',   bg: 'rgba(96,165,250,0.08)', border: 'rgba(96,165,250,0.2)'  },
  MEMBER: { Icon: User,   color: TT.inkSubtle, bg: TT.inkRaised,           border: TT.inkBorder             },
  VIEWER: { Icon: Eye,    color: TT.inkMuted,  bg: 'rgba(127,127,127,0.08)', border: 'rgba(127,127,127,0.2)' },
  owner:  { Icon: Crown,  color: TT.yolk,    bg: 'rgba(245,230,66,0.08)',  border: 'rgba(245,230,66,0.2)'  },
  admin:  { Icon: Shield, color: '#60A5FA',   bg: 'rgba(96,165,250,0.08)', border: 'rgba(96,165,250,0.2)'  },
  member: { Icon: User,   color: TT.inkSubtle, bg: TT.inkRaised,           border: TT.inkBorder             },
} as const;

// FIX: Safe wrapper — returns 'unknown' instead of throwing on missing/invalid dates.
// Handles Date objects, ISO strings, and undefined/null from the API.
function safeFromNow(date: Date | string | undefined | null): string {
  if (!date) return 'unknown';
  const d = new Date(date);
  return isNaN(d.getTime()) ? 'unknown' : formatDistanceToNow(d, { addSuffix: true });
}

/* ─── Primitives ───────────────────────────────────────────────────────── */

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 7 }}>
      <span style={{ width: 4, height: 4, borderRadius: '50%', background: TT.yolk, display: 'inline-block', boxShadow: '0 0 6px rgba(245,230,66,0.6)' }} />
      <label style={{ fontFamily: TT.fontMono, fontSize: 9.5, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted }}>
        {children}
      </label>
    </div>
  );
}

function TTInput({ value, onChange, placeholder, type = 'text' }: { value: string; onChange: (v: string) => void; placeholder?: string; type?: string }) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      style={{
        width: '100%', height: 42,
        background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3,
        color: TT.snow, fontFamily: TT.fontMono, fontSize: 13, letterSpacing: '0.02em',
        padding: '0 12px', outline: 'none', boxSizing: 'border-box', transition: 'border-color 0.15s, box-shadow 0.15s',
      }}
      onFocus={(e) => { (e.target as HTMLInputElement).style.borderColor = TT.yolk; (e.target as HTMLInputElement).style.boxShadow = '0 0 0 3px rgba(245,230,66,0.1)'; }}
      onBlur={(e) => { (e.target as HTMLInputElement).style.borderColor = TT.inkBorder; (e.target as HTMLInputElement).style.boxShadow = 'none'; }}
    />
  );
}

function TTTextarea({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      rows={3}
      style={{
        width: '100%', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3,
        color: TT.snow, fontFamily: TT.fontBody, fontSize: 13, lineHeight: 1.6,
        padding: '10px 12px', outline: 'none', resize: 'none', boxSizing: 'border-box', transition: 'border-color 0.15s, box-shadow 0.15s',
      }}
      onFocus={(e) => { (e.target as HTMLTextAreaElement).style.borderColor = TT.yolk; (e.target as HTMLTextAreaElement).style.boxShadow = '0 0 0 3px rgba(245,230,66,0.1)'; }}
      onBlur={(e) => { (e.target as HTMLTextAreaElement).style.borderColor = TT.inkBorder; (e.target as HTMLTextAreaElement).style.boxShadow = 'none'; }}
    />
  );
}

function YellowBtn({ onClick, children, disabled }: { onClick?: () => void; children: React.ReactNode; disabled?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        height: 38, padding: '0 18px',
        background: disabled ? TT.inkRaised : TT.yolk,
        border: `2px solid ${disabled ? TT.inkBorder : TT.yolk}`,
        borderRadius: 3, color: disabled ? TT.inkMuted : TT.inkBlack,
        fontFamily: TT.fontDisplay, fontSize: 15, letterSpacing: '0.1em', textTransform: 'uppercase',
        cursor: disabled ? 'not-allowed' : 'pointer',
        display: 'flex', alignItems: 'center', gap: 6, transition: 'all 0.15s',
      }}
      onMouseEnter={(e) => { if (!disabled) { (e.currentTarget as HTMLElement).style.background = TT.yolkBright; (e.currentTarget as HTMLElement).style.borderColor = TT.yolkBright; } }}
      onMouseLeave={(e) => { if (!disabled) { (e.currentTarget as HTMLElement).style.background = TT.yolk; (e.currentTarget as HTMLElement).style.borderColor = TT.yolk; } }}
    >
      {children}
    </button>
  );
}

function GhostBtn({ onClick, children }: { onClick?: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      style={{
        height: 38, padding: '0 18px',
        background: 'transparent', border: `1px solid ${TT.inkBorder}`, borderRadius: 3,
        color: TT.inkMuted, fontFamily: TT.fontDisplay, fontSize: 15, letterSpacing: '0.1em', textTransform: 'uppercase',
        cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, transition: 'all 0.15s',
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.3)'; (e.currentTarget as HTMLElement).style.color = TT.yolk; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder; (e.currentTarget as HTMLElement).style.color = TT.inkMuted; }}
    >
      {children}
    </button>
  );
}

function SmallBtn({ onClick, children, danger }: { onClick?: () => void; children: React.ReactNode; danger?: boolean }) {
  return (
    <button
      onClick={onClick}
      style={{
        background: 'none', border: `1px solid ${TT.inkBorder}`, borderRadius: 2,
        cursor: 'pointer', padding: '4px 6px',
        color: TT.inkMuted, display: 'flex', alignItems: 'center', gap: 4, transition: 'all 0.15s',
        fontFamily: TT.fontMono, fontSize: 9.5, letterSpacing: '0.04em',
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.color = danger ? TT.error : TT.yolk;
        (e.currentTarget as HTMLElement).style.borderColor = danger ? 'rgba(255,69,69,0.3)' : 'rgba(245,230,66,0.3)';
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.color = TT.inkMuted;
        (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder;
      }}
    >
      {children}
    </button>
  );
}

function TTDialog({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode }) {
  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent
        style={{
          background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`,
          borderTop: `3px solid ${TT.yolk}`, borderRadius: 4,
          maxWidth: 640, maxHeight: '90vh', overflow: 'auto',
          fontFamily: TT.fontMono, color: TT.snow,
        }}
      >
        <DialogHeader>
          <DialogTitle style={{ fontFamily: TT.fontDisplay, fontSize: 28, letterSpacing: '0.06em', color: TT.snow }}>
            <span style={{ color: TT.yolk }}>{title.charAt(0)}</span>{title.slice(1)}
          </DialogTitle>
        </DialogHeader>
        <div style={{ marginTop: 20 }}>{children}</div>
      </DialogContent>
    </Dialog>
  );
}

/* ─── WorkspacesView ───────────────────────────────────────────────────── */

export function WorkspacesView({ user }: WorkspacesViewProps) {
  const [workspaces, setWorkspaces] = useState<any[]>([]);
  const [isCreating, setIsCreating] = useState(false);
  const [editingWorkspace, setEditingWorkspace] = useState<any | null>(null);
  const [selectedWorkspace, setSelectedWorkspace] = useState<any | null>(null);
  const [newWorkspace, setNewWorkspace] = useState({ name: '', description: '' });
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [workspaceMembers, setWorkspaceMembers] = useState<any[]>([]);
  const [workspaceNotes, setWorkspaceNotes] = useState<any[]>([]);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<string>('MEMBER');
  const [inviteDialogOpen, setInviteDialogOpen] = useState(false);
  
  const authContext = useContext(AuthContext);
  const currentUser = user || authContext?.user;

  useEffect(() => {
    loadWorkspaces();
  }, []);

  useEffect(() => {
    if (selectedWorkspace) {
      loadWorkspaceMembers(selectedWorkspace.id);
      loadWorkspaceNotes(selectedWorkspace.id);
    }
  }, [selectedWorkspace?.id]);

  // FIX: Centralised date-safe transform — falls back to Date.now() so
  // createdAt/updatedAt are always valid Date objects inside state.
  const transformWorkspace = (workspace: any) => {
    const createdAtRaw = workspace.created_at || workspace.createdAt;
    const updatedAtRaw = workspace.updated_at || workspace.updatedAt;
    const createdAt = new Date(createdAtRaw ?? Date.now());
    const updatedAt = new Date(updatedAtRaw ?? Date.now());
    return {
      ...workspace,
      createdAt: isNaN(createdAt.getTime()) ? new Date() : createdAt,
      updatedAt: isNaN(updatedAt.getTime()) ? new Date() : updatedAt,
      members: workspace.members || [],
    };
  };

  const loadWorkspaces = async () => {
    console.log('📂 [LOAD WORKSPACES START] Fetching all workspaces');
    try {
      setIsLoading(true);
      console.debug('📡 [API CALL] Calling api.listWorkspaces()');
      const response: any = await api.listWorkspaces();
      console.debug('✅ [API RESPONSE] Received workspaces data');
      
      const items = Array.isArray(response) ? response : response.items || [];
      console.log('📦 [TRANSFORM] Processing %d workspaces', items.length);
      setWorkspaces(items.map(transformWorkspace));
      console.log('✅ [LOAD WORKSPACES SUCCESS] Loaded %d workspaces', items.length);
    } catch (err) {
      console.error('❌ [LOAD WORKSPACES FAILED] Failed to load workspaces:', err);
      setError('Failed to load workspaces');
    } finally {
      setIsLoading(false);
    }
  };

  const loadWorkspaceMembers = async (workspaceId: string) => {
    console.log('👥 [LOAD MEMBERS START] Fetching members for workspace:', workspaceId);
    try {
      console.debug('📡 [API CALL] Calling api.getWorkspaceMembers()');
      const members = await api.getWorkspaceMembers(workspaceId);
      console.log('✅ [LOAD MEMBERS SUCCESS] Loaded %d members', members.length);
      setWorkspaceMembers(members);
    } catch (err) {
      console.error('❌ [LOAD MEMBERS FAILED] Failed to load members for workspace %s:', workspaceId, err);
    }
  };

  const loadWorkspaceNotes = async (workspaceId: string) => {
    console.log('📝 [LOAD NOTES START] Fetching notes for workspace:', workspaceId);
    try {
      console.debug('📡 [API CALL] Calling api.getWorkspaceNotes()');
      const response: any = await api.getWorkspaceNotes(workspaceId, { page_size: 10 });
      console.debug('✅ [API RESPONSE] Received notes data');
      
      const notes = Array.isArray(response) ? response : response.items || [];
      console.log('✅ [LOAD NOTES SUCCESS] Loaded %d notes', notes.length);
      setWorkspaceNotes(notes);
    } catch (err) {
      console.error('❌ [LOAD NOTES FAILED] Failed to load workspace notes for %s:', workspaceId, err);
    }
  };

  // Calculate total connections from all notes in workspace
  const calculateConnections = (): number => {
    const connectionIds = new Set<string>();
    (workspaceNotes || []).forEach((note: any) => {
      const connections = note.connections || [];
      connections.forEach((connId: string) => connectionIds.add(connId));
    });
    return connectionIds.size;
  };

  const handleCreateWorkspace = async () => {
    console.log('✨ [CREATE WORKSPACE START] Creating new workspace - Name:', newWorkspace.name);
    if (!newWorkspace.name.trim()) {
      console.warn('⚠️ [VALIDATION] Workspace name is empty');
      return;
    }
    try {
      setIsLoading(true);
      console.debug('📡 [API CALL] Calling api.createWorkspace()');
      const created = await api.createWorkspace({
        name: newWorkspace.name,
        description: newWorkspace.description,
      });
      console.log('✅ [WORKSPACE CREATED] New workspace ID:', created.id);
      setWorkspaces([transformWorkspace(created), ...workspaces]);
      setIsCreating(false);
      setNewWorkspace({ name: '', description: '' });
      setError(null);
      console.log('✅ [CREATE WORKSPACE SUCCESS] Workspace created and state updated');
    } catch (err) {
      console.error('❌ [CREATE WORKSPACE FAILED] Failed to create workspace:', err);
      setError('Failed to create workspace');
    } finally {
      setIsLoading(false);
    }
  };

  const handleUpdateWorkspace = async (id: string, updates: any) => {
    console.log('✏️ [UPDATE WORKSPACE START] Updating workspace:', id);
    if (!id) {
      console.warn('⚠️ [VALIDATION] Workspace ID is missing');
      return;
    }
    try {
      setIsLoading(true);
      console.debug('📡 [API CALL] Calling api.updateWorkspace()');
      const updated = await api.updateWorkspace(id, {
        name: updates.name,
        description: updates.description,
      });
      console.log('✅ [WORKSPACE UPDATED] Workspace updated successfully');
      const safe = transformWorkspace(updated);
      setWorkspaces(workspaces.map(w => w.id === safe.id ? safe : w));
      setEditingWorkspace(null);
      if (selectedWorkspace?.id === safe.id) setSelectedWorkspace(safe);
      setError(null);
      console.log('✅ [UPDATE WORKSPACE SUCCESS] State updated');
    } catch (err) {
      console.error('❌ [UPDATE WORKSPACE FAILED] Failed to update workspace %s:', id, err);
      setError('Failed to update workspace');
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteWorkspace = async (id: string) => {
    console.log('🗑️ [DELETE WORKSPACE START] Deleting workspace:', id);
    if (!confirm('Are you sure? This action cannot be undone.')) {
      console.log('ℹ️ [CANCELLED] Delete operation cancelled by user');
      return;
    }
    try {
      setIsLoading(true);
      console.debug('📡 [API CALL] Calling api.deleteWorkspace()');
      await api.deleteWorkspace(id);
      console.log('✅ [WORKSPACE DELETED] Workspace deleted from backend');
      setWorkspaces(workspaces.filter(w => w.id !== id));
      if (selectedWorkspace?.id === id) setSelectedWorkspace(null);
      setError(null);
      console.log('✅ [DELETE WORKSPACE SUCCESS] State updated and workspace removed from list');
    } catch (err) {
      console.error('❌ [DELETE WORKSPACE FAILED] Failed to delete workspace %s:', id, err);
      setError('Failed to delete workspace');
    } finally {
      setIsLoading(false);
    }
  };

  const handleInviteMember = async () => {
    console.log('👥 [INVITE MEMBER START] Inviting member to workspace:', selectedWorkspace?.id);
    if (!selectedWorkspace || !inviteEmail.trim()) {
      console.warn('⚠️ [VALIDATION] Workspace or email is missing');
      return;
    }
    try {
      setIsLoading(true);
      console.debug('📡 [API CALL] Inviting %s with role %s', inviteEmail, inviteRole);
      await api.inviteToWorkspace(selectedWorkspace.id, inviteEmail, inviteRole as 'owner' | 'admin' | 'member' | 'viewer');
      console.log('✅ [INVITE SENT] Invitation sent successfully');
      await loadWorkspaceMembers(selectedWorkspace.id);
      setInviteEmail('');
      setInviteDialogOpen(false);
      setError(null);
      console.log('✅ [INVITE MEMBER SUCCESS] Member list updated');
    } catch (err) {
      console.error('❌ [INVITE MEMBER FAILED] Failed to invite member to workspace %s:', selectedWorkspace?.id, err);
      setError('Failed to invite member');
    } finally {
      setIsLoading(false);
    }
  };

  const handleRemoveMember = async (memberId: string) => {
    console.log('👤 [REMOVE MEMBER START] Removing member from workspace:', memberId);
    if (!selectedWorkspace || !confirm('Remove this member?')) {
      console.log('ℹ️ [CANCELLED] Remove member operation cancelled');
      return;
    }
    try {
      setIsLoading(true);
      console.debug('📡 [API CALL] Calling api.removeMember()');
      await api.removeMember(selectedWorkspace.id, memberId);
      console.log('✅ [MEMBER REMOVED] Member removed from workspace');
      await loadWorkspaceMembers(selectedWorkspace.id);
      setError(null);
      console.log('✅ [REMOVE MEMBER SUCCESS] Member list updated');
    } catch (err) {
      console.error('❌ [REMOVE MEMBER FAILED] Failed to remove member %s:', memberId, err);
      setError('Failed to remove member');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div style={{ padding: 32, background: TT.inkBlack, minHeight: '100vh', fontFamily: TT.fontMono }}>

      {/* ── Header ─────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 32, flexWrap: 'wrap', gap: 16 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ width: 4, height: 4, borderRadius: '50%', background: TT.yolk, display: 'inline-block', boxShadow: '0 0 6px rgba(245,230,66,0.8)' }} />
            <span style={{ fontFamily: TT.fontMono, fontSize: 9.5, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted }}>
              Collaboration
            </span>
          </div>
          <h1 style={{ fontFamily: TT.fontDisplay, fontSize: 44, letterSpacing: '0.04em', color: TT.snow, lineHeight: 0.9, textTransform: 'uppercase' }}>
            <span style={{ color: TT.yolk }}>W</span>ORKSPACES
          </h1>
          <div style={{ width: 36, height: 3, background: TT.yolk, marginTop: 10 }} />
          <p style={{ fontFamily: TT.fontMono, fontSize: 10.5, color: TT.inkMuted, marginTop: 10, letterSpacing: '0.05em', textTransform: 'uppercase' }}>
            {workspaces.length} collaborative spaces
          </p>
        </div>
        <YellowBtn onClick={() => setIsCreating(true)}>
          <Plus size={13} /> New Workspace
        </YellowBtn>
      </div>

      {/* ── Workspace Grid ─────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
        {error && (
          <div style={{ gridColumn: '1 / -1', background: 'rgba(255,69,69,0.1)', border: '1px solid rgba(255,69,69,0.3)', borderRadius: 3, padding: '12px 16px', color: TT.error, fontFamily: TT.fontMono, fontSize: 11 }}>
            {error}
          </div>
        )}

        {isLoading && workspaces.length === 0 ? (
          <div style={{ gridColumn: '1 / -1', textAlign: 'center', padding: '60px 0', color: TT.inkMuted, fontFamily: TT.fontMono }}>
            Loading workspaces...
          </div>
        ) : workspaces.length === 0 ? (
          <div style={{ gridColumn: '1 / -1', textAlign: 'center', padding: '60px 0' }}>
            <p style={{ fontFamily: TT.fontDisplay, fontSize: 24, color: TT.snow, marginBottom: 8 }}>NO WORKSPACES</p>
            <p style={{ fontFamily: TT.fontMono, fontSize: 10.5, color: TT.inkMuted, textTransform: 'uppercase' }}>Create one to get started</p>
          </div>
        ) : null}

        {workspaces.map((workspace, index) => (
          <motion.div
            key={workspace.id}
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: index * 0.06 }}
          >
            <div
              onClick={() => setSelectedWorkspace(workspace)}
              style={{
                background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderRadius: 3,
                padding: '18px 16px', cursor: 'pointer',
                transition: 'border-color 0.15s, border-left-width 0.1s', position: 'relative',
              }}
              onMouseEnter={(e) => {
                const el = e.currentTarget as HTMLElement;
                el.style.borderColor = 'rgba(245,230,66,0.2)';
                el.style.borderLeftColor = TT.yolk;
                el.style.borderLeftWidth = '3px';
                el.querySelectorAll<HTMLElement>('.ws-actions').forEach(b => b.style.opacity = '1');
              }}
              onMouseLeave={(e) => {
                const el = e.currentTarget as HTMLElement;
                el.style.borderColor = TT.inkBorder;
                el.style.borderLeftColor = TT.inkBorder;
                el.style.borderLeftWidth = '1px';
                el.querySelectorAll<HTMLElement>('.ws-actions').forEach(b => b.style.opacity = '0');
              }}
            >
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14 }}>
                <div style={{ width: 40, height: 40, borderRadius: 3, background: 'rgba(245,230,66,0.07)', border: '1px solid rgba(245,230,66,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <FolderOpen size={18} color={TT.yolk} />
                </div>
                <div className="ws-actions" style={{ display: 'flex', gap: 4, opacity: 0, transition: 'opacity 0.15s' }} onClick={(e) => e.stopPropagation()}>
                  <SmallBtn onClick={() => setEditingWorkspace(workspace)}><Edit2 size={10} /></SmallBtn>
                  <SmallBtn danger onClick={() => handleDeleteWorkspace(workspace.id)}><Trash2 size={10} /></SmallBtn>
                </div>
              </div>

              <h3 style={{ fontFamily: TT.fontMono, fontSize: 13, fontWeight: 500, color: TT.snow, marginBottom: 6, letterSpacing: '0.02em' }}>
                {workspace.name}
              </h3>
              <p style={{ fontFamily: TT.fontBody, fontSize: 11.5, color: TT.inkMuted, lineHeight: 1.6, marginBottom: 14, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                {workspace.description || '(No description)'}
              </p>

              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                  <Users size={10} color={TT.inkMuted} />
                  <span style={{ fontFamily: TT.fontMono, fontSize: 9.5, color: TT.inkMuted, letterSpacing: '0.04em' }}>
                    {workspace.members?.length || 1} member{(workspace.members?.length || 1) !== 1 ? 's' : ''}
                  </span>
                </div>
                {/* FIX: safeFromNow never throws on missing/invalid dates */}
                <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMid, letterSpacing: '0.03em' }}>
                  {safeFromNow(workspace.createdAt)}
                </span>
              </div>
            </div>
          </motion.div>
        ))}

        {/* ── Create New Card ──────────────────────────────────── */}
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: workspaces.length * 0.06 }}>
          <div
            onClick={() => setIsCreating(true)}
            style={{
              background: 'transparent', border: `1px dashed ${TT.inkBorder}`, borderRadius: 3,
              padding: '18px 16px', cursor: 'pointer', minHeight: 180,
              display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10,
              transition: 'border-color 0.15s, background 0.15s',
            }}
            onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.3)'; (e.currentTarget as HTMLElement).style.background = 'rgba(245,230,66,0.03)'; }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder; (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
          >
            <div style={{ width: 36, height: 36, borderRadius: 3, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Plus size={16} color={TT.inkMuted} />
            </div>
            <span style={{ fontFamily: TT.fontDisplay, fontSize: 16, letterSpacing: '0.08em', color: TT.inkMuted }}>NEW WORKSPACE</span>
            <span style={{ fontFamily: TT.fontMono, fontSize: 9.5, color: TT.inkMid, letterSpacing: '0.04em', textTransform: 'uppercase' }}>Start a collaborative space</span>
          </div>
        </motion.div>
      </div>

      {/* ── Workspace Detail Dialog ─────────────────────────────── */}
      <TTDialog open={!!selectedWorkspace} onClose={() => setSelectedWorkspace(null)} title={selectedWorkspace?.name ?? ''}>
        {selectedWorkspace && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            <p style={{ fontFamily: TT.fontBody, fontSize: 13, color: TT.inkMuted, lineHeight: 1.65 }}>
              {selectedWorkspace.description}
            </p>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
              {[
                { label: 'Members',     value: workspaceMembers.length },
                { label: 'Notes',       value: workspaceNotes.length  },
                { label: 'Connections', value: calculateConnections() },
              ].map(({ label, value }) => (
                <div key={label} style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderLeft: `3px solid ${TT.yolk}`, borderRadius: 3, padding: '12px 14px', textAlign: 'center' }}>
                  <div style={{ fontFamily: TT.fontDisplay, fontSize: 32, color: TT.snow, letterSpacing: '0.02em', lineHeight: 1 }}>{value}</div>
                  <div style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginTop: 4 }}>{label}</div>
                </div>
              ))}
            </div>

            {/* Members */}
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <Users size={12} color={TT.yolk} />
                  <span style={{ fontFamily: TT.fontDisplay, fontSize: 17, letterSpacing: '0.06em', color: TT.snow }}>MEMBERS</span>
                </div>
                <SmallBtn onClick={() => setInviteDialogOpen(true)}><UserPlus size={10} /> Invite</SmallBtn>
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {workspaceMembers.map((member) => {
                  const role = member.role.toUpperCase() as keyof typeof roleConfig;
                  const rc = roleConfig[role] ?? roleConfig.MEMBER;
                  const { Icon: RoleIcon } = rc;
                  const displayName = member.full_name || member.email || 'Unknown User';
                  const initial = (member.full_name?.charAt(0) || member.email?.charAt(0) || 'U') ?? 'U';
                  const isCurrentUser = member.user_id === currentUser?.id;

                  return (
                    <div key={member.user_id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '10px 12px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <div style={{ width: 32, height: 32, borderRadius: 3, background: role === 'OWNER' ? TT.yolk : TT.inkMid, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: TT.fontDisplay, fontSize: 16, color: role === 'OWNER' ? TT.inkBlack : TT.snow, flexShrink: 0 }}>
                          {initial.toUpperCase()}
                        </div>
                        <div>
                          <p style={{ fontFamily: TT.fontMono, fontSize: 11.5, color: TT.snow, letterSpacing: '0.02em' }}>
                            {displayName} {isCurrentUser && <span style={{ color: TT.inkMuted }}>(you)</span>}
                          </p>
                          <p style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMuted, letterSpacing: '0.03em' }}>{member.email}</p>
                        </div>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.07em', textTransform: 'uppercase', padding: '2px 8px', background: rc.bg, color: rc.color, border: `1px solid ${rc.border}`, borderRadius: 2 }}>
                          <RoleIcon size={9} /> {role}
                        </span>
                        {!isCurrentUser && <SmallBtn danger onClick={() => handleRemoveMember(member.user_id)}><Trash2 size={10} /></SmallBtn>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Notes */}
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 12 }}>
                <FileText size={12} color={TT.yolk} />
                <span style={{ fontFamily: TT.fontDisplay, fontSize: 17, letterSpacing: '0.06em', color: TT.snow }}>NOTES</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {workspaceNotes.length > 0 ? (
                  workspaceNotes.map((note: any) => {
                    const noteCreatedAt = note.created_at || note.createdAt;
                    const displayTime = noteCreatedAt ? formatDistanceToNow(new Date(noteCreatedAt), { addSuffix: true }) : 'unknown';
                    return (
                      <div key={note.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '10px 12px' }}>
                        <div style={{ width: 28, height: 28, borderRadius: 2, background: TT.inkBlack, border: `1px solid ${TT.inkBorder}`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                          <FileText size={12} color={TT.inkMuted} />
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <p style={{ fontFamily: TT.fontMono, fontSize: 11, color: TT.snow, letterSpacing: '0.02em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{note.title}</p>
                          <p style={{ fontFamily: TT.fontMono, fontSize: 9.5, color: TT.inkMuted, letterSpacing: '0.02em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{note.content?.substring(0, 60)}...</p>
                        </div>
                        <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMid, letterSpacing: '0.03em', flexShrink: 0 }}>{displayTime}</span>
                      </div>
                    );
                  })
                ) : (
                  <div style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.inkMuted, textAlign: 'center', padding: '12px' }}>
                    No notes yet
                  </div>
                )}
              </div>
            </div>

            {/* Recent Notes */}
            {workspaceNotes.length > 0 && (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 12 }}>
                  <Clock size={12} color={TT.yolk} />
                  <span style={{ fontFamily: TT.fontDisplay, fontSize: 17, letterSpacing: '0.06em', color: TT.snow }}>RECENTLY ADDED</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {workspaceNotes.slice(0, 3).map((note: any) => {
                    const createdTime = note.created_at || note.createdAt;
                    const displayTime = createdTime ? formatDistanceToNow(new Date(createdTime), { addSuffix: true }) : 'unknown';
                    return (
                      <div key={note.id} style={{ display: 'flex', alignItems: 'center', gap: 10, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '10px 12px' }}>
                        <div style={{ width: 28, height: 28, borderRadius: 2, background: TT.inkBlack, border: `1px solid ${TT.inkBorder}`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                          <FileText size={12} color={TT.inkMuted} />
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <p style={{ fontFamily: TT.fontMono, fontSize: 11, color: TT.snow, letterSpacing: '0.02em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{note.title}</p>
                          <p style={{ fontFamily: TT.fontMono, fontSize: 9.5, color: TT.inkMuted, letterSpacing: '0.02em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{note?.content?.substring(0, 40)}...</p>
                        </div>
                        <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMid, letterSpacing: '0.03em', flexShrink: 0 }}>{displayTime}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </TTDialog>

      {/* ── Create Workspace Dialog ────────────────────────────── */}
      <TTDialog open={isCreating} onClose={() => setIsCreating(false)} title="New Workspace">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div>
            <FieldLabel>Name</FieldLabel>
            <TTInput value={newWorkspace.name} onChange={(v) => setNewWorkspace({ ...newWorkspace, name: v })} placeholder="e.g., Product Team" />
          </div>
          <div>
            <FieldLabel>Description</FieldLabel>
            <TTTextarea value={newWorkspace.description} onChange={(v) => setNewWorkspace({ ...newWorkspace, description: v })} placeholder="What is this workspace for?" />
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <GhostBtn onClick={() => setIsCreating(false)}>Cancel</GhostBtn>
            <YellowBtn onClick={handleCreateWorkspace} disabled={!newWorkspace.name.trim()}><Plus size={13} /> Create</YellowBtn>
          </div>
        </div>
      </TTDialog>

      {/* ── Edit Workspace Dialog ──────────────────────────────── */}
      <TTDialog open={!!editingWorkspace} onClose={() => setEditingWorkspace(null)} title="Edit Workspace">
        {editingWorkspace && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            <div>
              <FieldLabel>Name</FieldLabel>
              <TTInput value={editingWorkspace.name} onChange={(v) => setEditingWorkspace({ ...editingWorkspace, name: v })} />
            </div>
            <div>
              <FieldLabel>Description</FieldLabel>
              <TTTextarea value={editingWorkspace.description} onChange={(v) => setEditingWorkspace({ ...editingWorkspace, description: v })} />
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <GhostBtn onClick={() => setEditingWorkspace(null)}>Cancel</GhostBtn>
              <YellowBtn onClick={() => handleUpdateWorkspace(editingWorkspace.id, editingWorkspace)}>Save Changes</YellowBtn>
            </div>
          </div>
        )}
      </TTDialog>

      {/* ── Invite Member Dialog ───────────────────────────────── */}
      <TTDialog open={inviteDialogOpen} onClose={() => setInviteDialogOpen(false)} title="Invite Member">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div>
            <FieldLabel>Email Address</FieldLabel>
            <TTInput value={inviteEmail} onChange={setInviteEmail} placeholder="user@example.com" type="email" />
          </div>
          <div>
            <FieldLabel>Role</FieldLabel>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {['VIEWER', 'MEMBER', 'ADMIN', 'OWNER'].map((role) => (
                <label key={role} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                  <input type="radio" name="role" value={role} checked={inviteRole === role} onChange={(e) => setInviteRole(e.target.value)} style={{ margin: 0 }} />
                  <span style={{ fontFamily: TT.fontMono, fontSize: 12, color: TT.snow }}>{role}</span>
                  <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMuted }}>
                    {role === 'VIEWER' && '(View only)'}
                    {role === 'MEMBER' && '(Contribute)'}
                    {role === 'ADMIN' && '(Manage members)'}
                    {role === 'OWNER' && '(Full control)'}
                  </span>
                </label>
              ))}
            </div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <GhostBtn onClick={() => setInviteDialogOpen(false)}>Cancel</GhostBtn>
            <YellowBtn
              onClick={() => { if (selectedWorkspace && inviteEmail.trim()) handleInviteMember(); }}
              disabled={!inviteEmail.trim() || !selectedWorkspace}
            >
              Send Invite
            </YellowBtn>
          </div>
        </div>
      </TTDialog>
    </div>
  );
}