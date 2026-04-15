import { useState, useEffect, useContext } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  Plus, Search, Filter, Edit2, Trash2, Link2,
  Sparkles, Brain, Globe, Mic, FileText, X, Check, Tag, Users,
} from 'lucide-react';
import type { Note, NoteConnectionSuggestion, NoteVersion, Workspace } from '@/types';
import { formatDistanceToNow } from 'date-fns';
import { api } from '@/lib/api';
import { AuthContext } from '@/contexts/AuthContext';

interface NotesViewProps {
  notes?: Note[];
  workspaces?: Workspace[];
  selectedWorkspace?: string | null;
  onNoteCreate?: (note: Partial<Note>) => void;
  onNoteUpdate?: (id: string, note: Partial<Note>) => void;
  onNoteDelete?: (id: string) => void;
  onWorkspaceChange?: (workspaceId: string | null) => void;
}

const TT = {
  inkBlack:  '#0A0A0A',
  inkDeep:   '#111111',
  inkRaised: '#1A1A1A',
  inkBorder: '#252525',
  inkMid:    '#3A3A3A',
  inkMuted:  '#5A5A5A',
  inkSubtle: '#888888',
  inkDim:    '#6A6A6A',
  snow:      '#F5F5F5',
  yolk:      '#F5E642',
  yolkBright:'#FFF176',
  error:     '#FF4545',
  errorDim:  'rgba(255,69,69,0.08)',
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono:    "'IBM Plex Mono', monospace",
  fontBody:    "'IBM Plex Sans', sans-serif",
};

const noteTypeConfig = {
  note:          { icon: FileText, color: TT.inkSubtle, bg: TT.inkRaised,               label: 'Note'      },
  'web-clip':    { icon: Globe,    color: '#60A5FA',    bg: 'rgba(96,165,250,0.07)',      label: 'Web Clip'  },
  document:      { icon: FileText, color: '#A78BFA',    bg: 'rgba(167,139,250,0.07)',     label: 'Document'  },
  voice:         { icon: Mic,      color: '#FB923C',    bg: 'rgba(251,146,60,0.07)',      label: 'Voice'     },
  'ai-generated':{ icon: Sparkles, color: TT.yolk,      bg: 'rgba(245,230,66,0.07)',      label: 'AI'        },
} as const;

// FIX: safe wrapper — guards against missing/null/invalid dates from the API
function safeFromNow(date: Date | string | undefined | null): string {
  if (!date) return 'unknown';
  const d = new Date(date);
  return isNaN(d.getTime()) ? 'unknown' : formatDistanceToNow(d, { addSuffix: true });
}

function formatAbsoluteDate(date: Date | string | undefined | null): string {
  if (!date) return 'Unknown time';
  const value = new Date(date);
  return isNaN(value.getTime()) ? 'Unknown time' : value.toLocaleString();
}

function getVersionChangeLabel(version: NoteVersion): string {
  if (version.changeReason === 'created') return 'Created';
  if (version.changeReason === 'restored') return 'Restored';
  if (version.changeReason === 'updated') return 'Updated';
  return version.changeReason;
}

function toRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function toStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : [];
}

function toNumber(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function getVersionDiffStats(version: NoteVersion) {
  const metadata = toRecord(version.metadata);
  const summary = toRecord(metadata.summary);

  if (Object.keys(summary).length > 0) {
    return {
      wordsAdded: toNumber(summary.words_added),
      wordsDeleted: toNumber(summary.words_deleted),
      wordsUnchanged: toNumber(summary.words_unchanged),
      addedSegments: toNumber(summary.added_segments),
      deletedSegments: toNumber(summary.deleted_segments),
      unchangedSegments: toNumber(summary.unchanged_segments),
      changedSegments: toNumber(summary.changed_segments),
      wordDelta: toNumber(summary.word_delta),
      wordCount: toNumber(summary.word_count, version.wordCount || 0),
      previousWordCount: toNumber(summary.previous_word_count),
    };
  }

  return version.diffSegments.reduce(
    (acc, segment) => {
      if (segment.type === 'added') {
        acc.wordsAdded += segment.wordCount;
        acc.addedSegments += 1;
      } else if (segment.type === 'deleted') {
        acc.wordsDeleted += segment.wordCount;
        acc.deletedSegments += 1;
      } else {
        acc.wordsUnchanged += segment.wordCount;
        acc.unchangedSegments += 1;
      }
      acc.changedSegments = acc.addedSegments + acc.deletedSegments;
      acc.wordDelta = acc.wordsAdded - acc.wordsDeleted;
      acc.wordCount = version.wordCount || 0;
      return acc;
    },
    {
      wordsAdded: 0,
      wordsDeleted: 0,
      wordsUnchanged: 0,
      addedSegments: 0,
      deletedSegments: 0,
      unchangedSegments: 0,
      changedSegments: 0,
      wordDelta: 0,
      wordCount: version.wordCount || 0,
      previousWordCount: 0,
    }
  );
}

function getVersionMetadata(version: NoteVersion) {
  const metadata = toRecord(version.metadata);
  return {
    changedFields: toStringArray(metadata.changed_fields),
    tagDelta: toRecord(metadata.tag_delta),
    connectionDelta: toRecord(metadata.connection_delta),
    snapshotKind: typeof metadata.snapshot_kind === 'string' ? metadata.snapshot_kind : '',
    previousVersionNumber: toNumber(metadata.previous_version_number, -1),
    restoredFromVersionNumber: toNumber(metadata.restored_from_version_number, -1),
    trigger: typeof metadata.trigger === 'string' ? metadata.trigger : '',
  };
}

function getVersionFieldLabel(field: string): string {
  switch (field) {
    case 'created':
      return 'Initial snapshot';
    case 'title':
      return 'Title';
    case 'content':
      return 'Content';
    case 'tags':
      return 'Tags';
    case 'connections':
      return 'Connections';
    case 'note_type':
      return 'Type';
    case 'source_url':
      return 'Source link';
    default:
      return field.replace(/_/g, ' ');
  }
}

function formatVersionSnapshotSummary(version: NoteVersion): string {
  const metadata = getVersionMetadata(version);
  const stats = getVersionDiffStats(version);

  if (version.changeReason === 'created') {
    return 'Initial snapshot';
  }

  if (version.changeReason === 'restored') {
    return metadata.restoredFromVersionNumber > 0
      ? `Restored from v${metadata.restoredFromVersionNumber}`
      : 'Restored from history';
  }

  const parts: string[] = [];
  if (stats.wordsAdded > 0) parts.push(`+${stats.wordsAdded} words`);
  if (stats.wordsDeleted > 0) parts.push(`-${stats.wordsDeleted} words`);

  const changedFieldLabels = metadata.changedFields
    .filter((field) => field !== 'created')
    .map(getVersionFieldLabel);
  if (changedFieldLabels.length > 0) {
    parts.push(changedFieldLabels.slice(0, 2).join(' + '));
  }

  return parts[0] ? parts.join(' · ') : 'Saved revision';
}

function formatCountLabel(count: number, singular: string, plural?: string): string {
  return `${count} ${count === 1 ? singular : plural || `${singular}s`}`;
}

/* ─── Shared primitive components ─────────────────────────────────────── */

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 7 }}>
      <span
        style={{
          display: 'inline-block', width: 4, height: 4,
          borderRadius: '50%', background: TT.yolk,
          flexShrink: 0, boxShadow: '0 0 6px rgba(245,230,66,0.6)',
        }}
      />
      <label
        style={{
          fontFamily: TT.fontMono,
          fontSize: 9.5, letterSpacing: '0.1em',
          textTransform: 'uppercase', color: TT.inkMuted,
        }}
      >
        {children}
      </label>
    </div>
  );
}

function TTInput({
  value, onChange, placeholder, onKeyDown,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  onKeyDown?: React.KeyboardEventHandler<HTMLInputElement>;
}) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      onKeyDown={onKeyDown}
      style={{
        width: '100%', height: 42,
        background: TT.inkRaised,
        border: `1px solid ${TT.inkBorder}`,
        borderRadius: 3,
        color: TT.snow,
        fontFamily: TT.fontMono,
        fontSize: 13, letterSpacing: '0.02em',
        padding: '0 12px',
        outline: 'none',
        boxSizing: 'border-box',
        transition: 'border-color 0.15s, box-shadow 0.15s',
      }}
      onFocus={(e) => {
        (e.target as HTMLInputElement).style.borderColor = TT.yolk;
        (e.target as HTMLInputElement).style.boxShadow = '0 0 0 3px rgba(245,230,66,0.1)';
      }}
      onBlur={(e) => {
        (e.target as HTMLInputElement).style.borderColor = TT.inkBorder;
        (e.target as HTMLInputElement).style.boxShadow = 'none';
      }}
    />
  );
}

function TTTextarea({ value, onChange, placeholder }: {
  value: string; onChange: (v: string) => void; placeholder?: string;
}) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      rows={8}
      style={{
        width: '100%',
        background: TT.inkRaised,
        border: `1px solid ${TT.inkBorder}`,
        borderRadius: 3,
        color: TT.snow,
        fontFamily: TT.fontBody,
        fontSize: 13, lineHeight: 1.65,
        padding: '10px 12px',
        outline: 'none',
        resize: 'none',
        boxSizing: 'border-box',
        transition: 'border-color 0.15s, box-shadow 0.15s',
      }}
      onFocus={(e) => {
        (e.target as HTMLTextAreaElement).style.borderColor = TT.yolk;
        (e.target as HTMLTextAreaElement).style.boxShadow = '0 0 0 3px rgba(245,230,66,0.1)';
      }}
      onBlur={(e) => {
        (e.target as HTMLTextAreaElement).style.borderColor = TT.inkBorder;
        (e.target as HTMLTextAreaElement).style.boxShadow = 'none';
      }}
    />
  );
}

function YellowBtn({ onClick, children, disabled }: {
  onClick?: () => void; children: React.ReactNode; disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        height: 38, padding: '0 18px',
        background: disabled ? TT.inkRaised : TT.yolk,
        border: `2px solid ${disabled ? TT.inkBorder : TT.yolk}`,
        borderRadius: 3,
        color: disabled ? TT.inkMuted : TT.inkBlack,
        fontFamily: TT.fontDisplay,
        fontSize: 15, letterSpacing: '0.1em', textTransform: 'uppercase',
        cursor: disabled ? 'not-allowed' : 'pointer',
        display: 'flex', alignItems: 'center', gap: 6,
        transition: 'all 0.15s',
      }}
      onMouseEnter={(e) => {
        if (!disabled) {
          (e.currentTarget as HTMLElement).style.background = TT.yolkBright;
          (e.currentTarget as HTMLElement).style.borderColor = TT.yolkBright;
        }
      }}
      onMouseLeave={(e) => {
        if (!disabled) {
          (e.currentTarget as HTMLElement).style.background = TT.yolk;
          (e.currentTarget as HTMLElement).style.borderColor = TT.yolk;
        }
      }}
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
        background: 'transparent',
        border: `1px solid ${TT.inkBorder}`,
        borderRadius: 3,
        color: TT.inkMuted,
        fontFamily: TT.fontDisplay,
        fontSize: 15, letterSpacing: '0.1em', textTransform: 'uppercase',
        cursor: 'pointer',
        display: 'flex', alignItems: 'center', gap: 6,
        transition: 'all 0.15s',
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.3)';
        (e.currentTarget as HTMLElement).style.color = TT.yolk;
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder;
        (e.currentTarget as HTMLElement).style.color = TT.inkMuted;
      }}
    >
      {children}
    </button>
  );
}

function TagChip({ label, onRemove }: { label: string; onRemove?: () => void }) {
  return (
    <span
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        fontFamily: TT.fontMono,
        fontSize: 9, letterSpacing: '0.06em', textTransform: 'uppercase',
        padding: '2px 7px',
        background: 'rgba(245,230,66,0.08)',
        border: `1px solid rgba(245,230,66,0.2)`,
        borderRadius: 2,
        color: TT.yolk,
      }}
    >
      {label}
      {onRemove && (
        <button
          onClick={onRemove}
          style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: TT.yolk, padding: 0, display: 'flex',
          }}
        >
          <X size={9} />
        </button>
      )}
    </span>
  );
}

function TTDialog({
  open, onClose, title, children,
}: {
  open: boolean; onClose: () => void; title: string; children: React.ReactNode;
}) {
  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent
        style={{
          background: TT.inkDeep,
          border: `1px solid ${TT.inkBorder}`,
          borderTop: `3px solid ${TT.yolk}`,
          borderRadius: 4,
          maxWidth: 640,
          maxHeight: '90vh',
          overflow: 'auto',
          fontFamily: TT.fontMono,
          color: TT.snow,
        }}
      >
        <DialogHeader>
          <DialogTitle
            style={{
              fontFamily: TT.fontDisplay,
              fontSize: 28, letterSpacing: '0.06em',
              color: TT.snow,
            }}
          >
            <span style={{ color: TT.yolk }}>{title.charAt(0)}</span>
            {title.slice(1)}
          </DialogTitle>
        </DialogHeader>
        <div style={{ marginTop: 20 }}>{children}</div>
      </DialogContent>
    </Dialog>
  );
}

/* ─── NotesView ────────────────────────────────────────────────────────── */

export function NotesView({
  notes: initialNotes = [],
  workspaces: propWorkspaces,
  selectedWorkspace: initialSelectedWorkspace,
  onNoteCreate,
  onNoteUpdate,
  onNoteDelete,
  onWorkspaceChange = () => {},
}: NotesViewProps) {
  const authContext = useContext(AuthContext);
  const {
    currentWorkspaceId,
    workspaces: ctxWorkspaces,
    setCurrentWorkspace,
    hasPermission,
  } = authContext || {
    currentWorkspaceId: null,
    workspaces: [],
    setCurrentWorkspace: () => {},
    hasPermission: () => false,
  };
  const [notes, setNotes] = useState<Note[]>(initialNotes);
  const [workspaces, setWorkspaces] = useState<Workspace[]>(propWorkspaces || (ctxWorkspaces as any) || []);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedNote, setSelectedNote] = useState<Note | null>(null);
  const [selectedNoteWorkspace, setSelectedNoteWorkspace] = useState<Workspace | null>(null);
  const [selectedNoteWorkspaceMembers, setSelectedNoteWorkspaceMembers] = useState<any[]>([]);
  const [noteVersions, setNoteVersions] = useState<NoteVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [restoringVersionId, setRestoringVersionId] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [editingNote, setEditingNote] = useState<Note | null>(null);
  const [newTag, setNewTag] = useState('');
  const [connectionSuggestions, setConnectionSuggestions] = useState<NoteConnectionSuggestion[]>([]);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);

  const workspaceId = currentWorkspaceId || initialSelectedWorkspace;
  const defaultWorkspaceId = workspaceId || workspaces[0]?.id || '';
  const canViewWorkspaceNotes = Boolean(workspaceId && hasPermission(workspaceId, 'notes', 'view'));
  const canCreateWorkspaceNotes = Boolean(defaultWorkspaceId && hasPermission(defaultWorkspaceId, 'notes', 'create'));
  const canUpdateSelectedNote = Boolean(selectedNote?.workspaceId && hasPermission(selectedNote.workspaceId, 'notes', 'update'));

  const createEmptyNote = (targetWorkspaceId = '') => ({
    title: '',
    content: '',
    tags: [] as string[],
    workspaceId: targetWorkspaceId,
    noteType: 'note' as 'note' | 'web-clip' | 'document' | 'voice' | 'ai-generated',
  });

  const normalizeNote = (note: any): Note => ({
    ...note,
    userId: note.user_id || note.userId,
    workspaceId: note.workspace_id || note.workspaceId,
    createdAt: new Date(note.created_at || note.createdAt || Date.now()),
    updatedAt: new Date(note.updated_at || note.updatedAt || Date.now()),
    confidence: note.confidence_score || note.confidence,
    type: note.note_type || note.type || 'note',
    tags: Array.isArray(note.tags) ? note.tags : [],
    connections: Array.isArray(note.connections) ? note.connections.map(String) : [],
  });

  const [newNote, setNewNote] = useState(() => createEmptyNote(defaultWorkspaceId));
  
  const [showConnections, setShowConnections] = useState(false);
  const [connectingNote, setConnectingNote] = useState<Note | null>(null);

  useEffect(() => {
    const nextWorkspaces = propWorkspaces?.length ? propWorkspaces : ((ctxWorkspaces as any) || []);
    setWorkspaces(nextWorkspaces);
  }, [propWorkspaces, ctxWorkspaces]);

  useEffect(() => {
    if (!defaultWorkspaceId) return;

    setNewNote((prev) => {
      const hasValidWorkspaceSelection =
        !!prev.workspaceId && workspaces.some((ws) => ws.id === prev.workspaceId);

      if (hasValidWorkspaceSelection) {
        return prev;
      }

      return { ...prev, workspaceId: defaultWorkspaceId };
    });
  }, [defaultWorkspaceId, workspaces]);

  // Load notes from API
  useEffect(() => {
    if (!workspaceId || !canViewWorkspaceNotes) {
      setNotes([]);
      return;
    }

    const loadNotes = async () => {
      try {
        const response: any = await api.listNotes({ workspace_id: workspaceId });
        const items = Array.isArray(response) ? response : response.items || [];
        const transformedNotes = items.map(normalizeNote);
        setNotes(transformedNotes);
      } catch (err) {
        console.error('Failed to load notes:', err);
      }
    };

    loadNotes();
  }, [canViewWorkspaceNotes, workspaceId]);

  // Load workspace details for selected note
  useEffect(() => {
    const loadWorkspaceForNote = async () => {
      if (!selectedNote?.workspaceId) {
        setSelectedNoteWorkspace(null);
        setSelectedNoteWorkspaceMembers([]);
        return;
      }
      
      try {
        // First check if workspace is in the workspaces list
        const found = workspaces.find(w => w.id === selectedNote.workspaceId);
        if (found) {
          setSelectedNoteWorkspace(found);
        } else {
          // Try to fetch it from API
          const workspace: any = await api.getWorkspace(selectedNote.workspaceId);
          setSelectedNoteWorkspace(workspace);
        }
        
        // Load workspace members
        try {
          const members = await api.getWorkspaceMembers(selectedNote.workspaceId);
          setSelectedNoteWorkspaceMembers(members || []);
        } catch (err) {
          console.error('Failed to load workspace members:', err);
          setSelectedNoteWorkspaceMembers([]);
        }
      } catch (err) {
        console.error('Failed to load note workspace:', err);
        setSelectedNoteWorkspace(null);
        setSelectedNoteWorkspaceMembers([]);
      }
    };
    
    loadWorkspaceForNote();
  }, [selectedNote?.workspaceId, workspaces]);

  useEffect(() => {
    const loadConnectionSuggestions = async () => {
      if (!selectedNote?.id) {
        setConnectionSuggestions([]);
        return;
      }

      try {
        setSuggestionsLoading(true);
        const suggestions = await api.getNoteConnectionSuggestions(selectedNote.id);
        setConnectionSuggestions(suggestions);
      } catch (err) {
        console.error('Failed to load connection suggestions:', err);
        setConnectionSuggestions([]);
      } finally {
        setSuggestionsLoading(false);
      }
    };

    loadConnectionSuggestions();
  }, [selectedNote?.id]);

  useEffect(() => {
    const loadVersions = async () => {
      if (!selectedNote?.id) {
        setNoteVersions([]);
        setSelectedVersionId(null);
        return;
      }

      try {
        setVersionsLoading(true);
        const versions = await api.getNoteVersions(selectedNote.id);
        setNoteVersions(versions);
        setSelectedVersionId((current) => {
          if (current && versions.some((version) => version.id === current)) {
            return current;
          }
          return versions[0]?.id || null;
        });
      } catch (err) {
        console.error('Failed to load note versions:', err);
        setNoteVersions([]);
        setSelectedVersionId(null);
      } finally {
        setVersionsLoading(false);
      }
    };

    loadVersions();
  }, [selectedNote?.id]);

  const filteredNotes = notes.filter((note) => {
    const q = searchQuery.toLowerCase();
    return (
      note.title.toLowerCase().includes(q) ||
      note.content.toLowerCase().includes(q) ||
      note.tags.some((t) => t.toLowerCase().includes(q))
    );
  });

  const selectedVersion =
    noteVersions.find((version) => version.id === selectedVersionId) ||
    noteVersions[0] ||
    null;
  const latestVersion = noteVersions[0] || null;
  const selectedVersionIsLatest = Boolean(selectedVersion && latestVersion && selectedVersion.id === latestVersion.id);
  const selectedVersionStats = selectedVersion ? getVersionDiffStats(selectedVersion) : null;
  const selectedVersionMetadata = selectedVersion ? getVersionMetadata(selectedVersion) : null;
  const selectedVersionChangedFields = selectedVersionMetadata?.changedFields
    ?.filter((field) => field !== 'created')
    .map(getVersionFieldLabel) || [];
  const selectedVersionTagDelta = {
    added: toStringArray(selectedVersionMetadata?.tagDelta?.added),
    removed: toStringArray(selectedVersionMetadata?.tagDelta?.removed),
  };
  const selectedVersionConnectionDelta = {
    added: toStringArray(selectedVersionMetadata?.connectionDelta?.added),
    removed: toStringArray(selectedVersionMetadata?.connectionDelta?.removed),
  };

  const handleCreateNote = async () => {
    const targetWorkspaceId = newNote.workspaceId || defaultWorkspaceId;
    if (!newNote.title.trim() || !targetWorkspaceId) return;
    if (!hasPermission(targetWorkspaceId, 'notes', 'create')) return;

    try {
      const createdNoteResponse: any = await api.createNote({
        workspace_id: targetWorkspaceId,
        title: newNote.title,
        content: newNote.content,
        tags: newNote.tags,
        note_type: newNote.noteType,
      });
      const createdNote = normalizeNote(createdNoteResponse);

      if (targetWorkspaceId === workspaceId) {
        setNotes((prev) => [createdNote, ...prev]);
      }

      setNewNote(createEmptyNote(targetWorkspaceId));
      setNewTag('');
      setIsCreating(false);

      if (targetWorkspaceId !== workspaceId) {
        setCurrentWorkspace(targetWorkspaceId);
        onWorkspaceChange?.(targetWorkspaceId);
      }

      onNoteCreate?.({
        title: createdNote.title,
        content: createdNote.content,
        tags: createdNote.tags,
        userId: createdNote.userId,
        workspaceId: createdNote.workspaceId,
        connections: createdNote.connections || [],
        type: createdNote.type,
      });
    } catch (err) {
      console.error('Create note error:', err);
    }
  };

  const handleUpdateNote = async () => {
    if (!editingNote || !editingNote.workspaceId) return;
    if (!hasPermission(editingNote.workspaceId, 'notes', 'update')) return;

    try {
      const updatedNote: any = await api.updateNote(editingNote.id, {
        title: editingNote.title,
        content: editingNote.content,
        tags: editingNote.tags,
        note_type: editingNote.type,
      });
      const normalizedUpdatedNote = normalizeNote(updatedNote);

      setNotes(notes.map(n => n.id === normalizedUpdatedNote.id ? normalizedUpdatedNote : n) as Note[]);
      if (selectedNote?.id === normalizedUpdatedNote.id) {
        setSelectedNote(normalizedUpdatedNote);
      }
      if (selectedNote?.id === normalizedUpdatedNote.id) {
        const versions = await api.getNoteVersions(normalizedUpdatedNote.id);
        setNoteVersions(versions);
        setSelectedVersionId(versions[0]?.id || null);
      }
      setEditingNote(null);
      onNoteUpdate?.(normalizedUpdatedNote.id, normalizedUpdatedNote);
    } catch (err) {
      console.error('Update note error:', err);
    }
  };

  const handleRestoreVersion = async (version: NoteVersion) => {
    if (!selectedNote) return;
    if (!selectedNote.workspaceId || !hasPermission(selectedNote.workspaceId, 'notes', 'update')) return;

    try {
      setRestoringVersionId(version.id);
      const response = await api.restoreNoteVersion(selectedNote.id, version.id);
      const restoredNote = normalizeNote(response.note);

      setSelectedNote(restoredNote);
      setNotes((prev) => prev.map((note) => note.id === restoredNote.id ? restoredNote : note));

      const versions = await api.getNoteVersions(restoredNote.id);
      setNoteVersions(versions);
      setSelectedVersionId(versions[0]?.id || version.id);
      onNoteUpdate?.(restoredNote.id, restoredNote);
    } catch (err) {
      console.error('Restore note version error:', err);
    } finally {
      setRestoringVersionId(null);
    }
  };

  const handleUpdateConnections = async () => {
    if (!selectedNote) return;
    if (!selectedNote.workspaceId || !hasPermission(selectedNote.workspaceId, 'notes', 'update')) return;
    try {
      const updatedNote: any = await api.updateNote(selectedNote.id, {
        connections: selectedNote.connections || [],
      });
      const normalizedUpdatedNote = normalizeNote(updatedNote);

      setNotes(notes.map(n => n.id === normalizedUpdatedNote.id ? normalizedUpdatedNote : n) as Note[]);
      setSelectedNote(normalizedUpdatedNote);
      setShowConnections(false);
    } catch (err) {
      console.error('Update connections error:', err);
    }
  };

  const handleConfirmConnectionSuggestion = async (suggestion: NoteConnectionSuggestion) => {
    if (!selectedNote) return;
    if (!selectedNote.workspaceId || !hasPermission(selectedNote.workspaceId, 'notes', 'update')) return;

    try {
      const response = await api.confirmNoteConnectionSuggestion(selectedNote.id, suggestion.id);
      const nextConnections = response.connections || [];

      const updatedSelectedNote = { ...selectedNote, connections: nextConnections };
      setSelectedNote(updatedSelectedNote);
      setNotes((prev) =>
        prev.map((note) =>
          note.id === selectedNote.id ? { ...note, connections: nextConnections } : note
        )
      );
      setConnectionSuggestions((prev) => prev.filter((item) => item.id !== suggestion.id));
    } catch (err) {
      console.error('Confirm connection suggestion error:', err);
    }
  };

  const handleDismissConnectionSuggestion = async (suggestion: NoteConnectionSuggestion) => {
    if (!selectedNote) return;
    if (!selectedNote.workspaceId || !hasPermission(selectedNote.workspaceId, 'notes', 'update')) return;

    try {
      await api.dismissNoteConnectionSuggestion(selectedNote.id, suggestion.id);
      setConnectionSuggestions((prev) => prev.filter((item) => item.id !== suggestion.id));
    } catch (err) {
      console.error('Dismiss connection suggestion error:', err);
    }
  };

  const handleDeleteNote = async (noteId: string) => {
    const targetNote = notes.find((note) => note.id === noteId) || selectedNote;
    if (!targetNote?.workspaceId || !hasPermission(targetNote.workspaceId, 'notes', 'delete')) return;
    try {
      await api.deleteNote(noteId);
      setNotes(notes.filter(n => n.id !== noteId));
      setSelectedNote(null);
      onNoteDelete?.(noteId);
    } catch (err) {
      console.error('Delete note error:', err);
    }
  };

  const addTag = (isEditing: boolean) => {
    if (!newTag.trim()) return;
    if (isEditing && editingNote) {
      setEditingNote({ ...editingNote, tags: [...editingNote.tags, newTag.trim()] });
    } else {
      setNewNote({ ...newNote, tags: [...newNote.tags, newTag.trim()] });
    }
    setNewTag('');
  };

  const removeTag = (tag: string, isEditing: boolean) => {
    if (isEditing && editingNote) {
      setEditingNote({ ...editingNote, tags: editingNote.tags.filter((t) => t !== tag) });
    } else {
      setNewNote({ ...newNote, tags: newNote.tags.filter((t) => t !== tag) });
    }
  };

  return (
    <div style={{ padding: 32, background: TT.inkBlack, minHeight: '100vh', fontFamily: TT.fontMono }}>

      {/* ── Header ──────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 32, flexWrap: 'wrap', gap: 16 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ width: 4, height: 4, borderRadius: '50%', background: TT.yolk, display: 'inline-block', boxShadow: '0 0 6px rgba(245,230,66,0.8)' }} />
            <span style={{ fontFamily: TT.fontMono, fontSize: 9.5, letterSpacing: '0.1em', textTransform: 'uppercase', color: TT.inkMuted }}>
              Knowledge Base
            </span>
          </div>
          <h1
            style={{
              fontFamily: TT.fontDisplay, fontSize: 44,
              letterSpacing: '0.04em', color: TT.snow,
              lineHeight: 0.9, textTransform: 'uppercase',
            }}
          >
            <span style={{ color: TT.yolk }}>N</span>OTES
          </h1>
          <div style={{ width: 36, height: 3, background: TT.yolk, marginTop: 10 }} />
          <p style={{ fontFamily: TT.fontMono, fontSize: 10.5, color: TT.inkMuted, marginTop: 10, letterSpacing: '0.05em', textTransform: 'uppercase' }}>
            {filteredNotes.length} notes in your knowledge base
          </p>
        </div>
        <YellowBtn onClick={() => setIsCreating(true)} disabled={!canCreateWorkspaceNotes}>
          <Plus size={13} /> New Note
        </YellowBtn>
      </div>

      {!canViewWorkspaceNotes && workspaceId && (
        <div style={{ marginBottom: 20, padding: '12px 14px', background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderLeft: `3px solid ${TT.yolk}`, borderRadius: 3, color: TT.inkMuted, fontSize: 11, letterSpacing: '0.04em' }}>
          You can access this workspace, but your current role does not allow viewing notes here.
        </div>
      )}

      {/* ── Filters ─────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 24, flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: 200 }}>
          <Search
            size={13}
            color={TT.inkMuted}
            style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }}
          />
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search notes..."
            style={{
              width: '100%', height: 40,
              background: TT.inkRaised,
              border: `1px solid ${TT.inkBorder}`,
              borderRadius: 3,
              color: TT.snow,
              fontFamily: TT.fontMono,
              fontSize: 12, letterSpacing: '0.02em',
              paddingLeft: 34, paddingRight: 12,
              outline: 'none',
              boxSizing: 'border-box',
            }}
            onFocus={(e) => {
              (e.target as HTMLInputElement).style.borderColor = TT.yolk;
              (e.target as HTMLInputElement).style.boxShadow = '0 0 0 3px rgba(245,230,66,0.1)';
            }}
            onBlur={(e) => {
              (e.target as HTMLInputElement).style.borderColor = TT.inkBorder;
              (e.target as HTMLInputElement).style.boxShadow = 'none';
            }}
          />
        </div>

        <Select
          value={initialSelectedWorkspace || 'all'}
          onValueChange={(v) => onWorkspaceChange(v === 'all' ? null : v)}
        >
          <SelectTrigger
            style={{
              width: 200, height: 40,
              background: TT.inkRaised,
              border: `1px solid ${TT.inkBorder}`,
              borderRadius: 3,
              color: TT.inkMuted,
              fontFamily: TT.fontMono,
              fontSize: 11, letterSpacing: '0.05em', textTransform: 'uppercase',
            }}
          >
            <Filter size={11} style={{ marginRight: 6 }} />
            <SelectValue placeholder="All workspaces" />
          </SelectTrigger>
          <SelectContent style={{ background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderRadius: 3 }}>
            <SelectItem value="all">All workspaces</SelectItem>
            {workspaces.map((ws) => (
              <SelectItem key={ws.id} value={ws.id}>{ws.name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* ── Notes Grid ──────────────────────────────────────────── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
          gap: 10,
        }}
      >
        <AnimatePresence>
          {filteredNotes.map((note, index) => {
            const cfg = noteTypeConfig[note.type] ?? noteTypeConfig.note;
            const { icon: Icon } = cfg;

            return (
              <motion.div
                key={note.id}
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.97 }}
                transition={{ delay: index * 0.04 }}
              >
                <div
                  className="group"
                  onClick={() => setSelectedNote(note)}
                  style={{
                    background: TT.inkDeep,
                    border: `1px solid ${TT.inkBorder}`,
                    borderRadius: 3,
                    padding: '16px 16px',
                    cursor: 'pointer',
                    transition: 'border-color 0.15s, border-left-color 0.15s, border-left-width 0.1s',
                    position: 'relative',
                  }}
                  onMouseEnter={(e) => {
                    const el = e.currentTarget as HTMLElement;
                    el.style.borderColor = 'rgba(245,230,66,0.2)';
                    el.style.borderLeftColor = TT.yolk;
                    el.style.borderLeftWidth = '3px';
                    el.querySelectorAll<HTMLElement>('.note-actions').forEach(b => b.style.opacity = '1');
                  }}
                  onMouseLeave={(e) => {
                    const el = e.currentTarget as HTMLElement;
                    el.style.borderColor = TT.inkBorder;
                    el.style.borderLeftColor = TT.inkBorder;
                    el.style.borderLeftWidth = '1px';
                    el.querySelectorAll<HTMLElement>('.note-actions').forEach(b => b.style.opacity = '0');
                  }}
                >
                  {/* Top row */}
                  <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12 }}>
                    <div
                      style={{
                        width: 28, height: 28, borderRadius: 2,
                        background: cfg.bg,
                        border: `1px solid ${cfg.color}22`,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}
                    >
                      <Icon size={13} color={cfg.color} />
                    </div>

                    <div
                      className="note-actions"
                      style={{ display: 'flex', gap: 2, opacity: 0, transition: 'opacity 0.15s' }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        onClick={() => setEditingNote(note)}
                        disabled={!note.workspaceId || !hasPermission(note.workspaceId, 'notes', 'update')}
                        style={{
                          background: 'none', border: `1px solid ${TT.inkBorder}`,
                          borderRadius: 2, cursor: !note.workspaceId || !hasPermission(note.workspaceId, 'notes', 'update') ? 'not-allowed' : 'pointer', padding: '3px 5px',
                          color: TT.inkMuted, transition: 'all 0.15s', opacity: !note.workspaceId || !hasPermission(note.workspaceId, 'notes', 'update') ? 0.35 : 1,
                        }}
                        onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = TT.yolk; (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.3)'; }}
                        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = TT.inkMuted; (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder; }}
                      >
                        <Edit2 size={11} />
                      </button>
                      <button
                        onClick={() => handleDeleteNote(note.id)}
                        disabled={!note.workspaceId || !hasPermission(note.workspaceId, 'notes', 'delete')}
                        style={{
                          background: 'none', border: `1px solid ${TT.inkBorder}`,
                          borderRadius: 2, cursor: !note.workspaceId || !hasPermission(note.workspaceId, 'notes', 'delete') ? 'not-allowed' : 'pointer', padding: '3px 5px',
                          color: TT.inkMuted, transition: 'all 0.15s', opacity: !note.workspaceId || !hasPermission(note.workspaceId, 'notes', 'delete') ? 0.35 : 1,
                        }}
                        onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = TT.error; (e.currentTarget as HTMLElement).style.borderColor = 'rgba(255,69,69,0.3)'; }}
                        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = TT.inkMuted; (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder; }}
                      >
                        <Trash2 size={11} />
                      </button>
                    </div>
                  </div>

                  {/* Title */}
                  <h3
                    style={{
                      fontFamily: TT.fontMono,
                      fontSize: 13, fontWeight: 500,
                      color: TT.snow,
                      marginBottom: 6,
                      overflow: 'hidden', display: '-webkit-box',
                      WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                      letterSpacing: '0.02em',
                    }}
                  >
                    {note.title}
                  </h3>

                  {/* Excerpt */}
                  <p
                    style={{
                      fontFamily: TT.fontBody,
                      fontSize: 11.5, lineHeight: 1.6, color: TT.inkMuted,
                      marginBottom: 12,
                      overflow: 'hidden', display: '-webkit-box',
                      WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                    }}
                  >
                    {note.summary || note.content.substring(0, 100)}...
                  </p>

                  {/* Tags */}
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 12 }}>
                    {note.tags.slice(0, 3).map((tag) => (
                      <span
                        key={tag}
                        style={{
                          fontFamily: TT.fontMono,
                          fontSize: 8.5, letterSpacing: '0.05em', textTransform: 'uppercase',
                          padding: '1px 6px',
                          background: TT.inkRaised,
                          border: `1px solid ${TT.inkBorder}`,
                          borderRadius: 2,
                          color: TT.inkMuted,
                        }}
                      >
                        {tag}
                      </span>
                    ))}
                    {note.tags.length > 3 && (
                      <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, color: TT.inkMid }}>
                        +{note.tags.length - 3}
                      </span>
                    )}
                  </div>

                  {/* Footer — FIX: use safeFromNow instead of formatDistanceToNow */}
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 9, color: TT.inkMid, fontFamily: TT.fontMono, letterSpacing: '0.03em' }}>
                      <span>{safeFromNow(note.updatedAt)}</span>
                      {(note.word_count ?? 0) > 0 && (
                        <span style={{ display: 'flex', alignItems: 'center', gap: 3, padding: '1px 5px', background: 'rgba(245,230,66,0.05)', borderRadius: 2, color: TT.inkMuted }}>
                          📝 {note.word_count} words
                        </span>
                      )}
                    </div>
                    {note.confidence && (
                      <div
                        style={{
                          display: 'flex', alignItems: 'center', gap: 4,
                          padding: '2px 7px',
                          background: 'rgba(245,230,66,0.07)',
                          border: '1px solid rgba(245,230,66,0.15)',
                          borderRadius: 2,
                        }}
                      >
                        <Brain size={9} color={TT.yolk} />
                        <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.yolk }}>
                          {Math.round(note.confidence * 100)}%
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>

      {/* ── Create Note Dialog ─────────────────────────────────── */}
      <TTDialog open={isCreating} onClose={() => setIsCreating(false)} title="New Note">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div>
            <FieldLabel>Note Type</FieldLabel>
            <Select
              value={newNote.noteType}
              onValueChange={(v) => setNewNote({ ...newNote, noteType: v as typeof newNote.noteType })}
            >
              <SelectTrigger style={{ height: 42, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, color: TT.inkMuted, fontFamily: TT.fontMono, fontSize: 11, letterSpacing: '0.05em' }}>
                <SelectValue placeholder="Select note type" />
              </SelectTrigger>
              <SelectContent style={{ background: TT.inkDeep, border: `1px solid ${TT.inkBorder}` }}>
                {(Object.entries(noteTypeConfig) as Array<[keyof typeof noteTypeConfig, any]>).map(([key, cfg]) => {
                  const IconComponent = cfg.icon;
                  return (
                    <SelectItem key={key} value={key}>
                      <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <IconComponent size={11} color={cfg.color} />
                        {cfg.label}
                      </span>
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
          </div>
          <div>
            <FieldLabel>Title</FieldLabel>
            <TTInput
              value={newNote.title}
              onChange={(v) => setNewNote({ ...newNote, title: v })}
              placeholder="Enter note title..."
            />
          </div>
          <div>
            <FieldLabel>Content</FieldLabel>
            <TTTextarea
              value={newNote.content}
              onChange={(v) => setNewNote({ ...newNote, content: v })}
              placeholder="Start writing..."
            />
          </div>
          <div>
            <FieldLabel>Workspace</FieldLabel>
            <Select
              value={newNote.workspaceId}
              onValueChange={(v) => setNewNote({ ...newNote, workspaceId: v })}
            >
              <SelectTrigger style={{ height: 42, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, color: TT.inkMuted, fontFamily: TT.fontMono, fontSize: 11, letterSpacing: '0.05em' }}>
                <SelectValue placeholder="Select workspace" />
              </SelectTrigger>
              <SelectContent style={{ background: TT.inkDeep, border: `1px solid ${TT.inkBorder}` }}>
                {workspaces.map((ws) => (
                  <SelectItem key={ws.id} value={ws.id}>{ws.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <FieldLabel>Tags</FieldLabel>
            <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
              <TTInput
                value={newTag}
                onChange={setNewTag}
                placeholder="Add tag and press Enter..."
                onKeyDown={(e) => e.key === 'Enter' && addTag(false)}
              />
              <button
                onClick={() => addTag(false)}
                style={{ height: 42, width: 42, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, cursor: 'pointer', color: TT.inkMuted, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = TT.yolk; (e.currentTarget as HTMLElement).style.borderColor = 'rgba(245,230,66,0.3)'; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = TT.inkMuted; (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder; }}
              >
                <Plus size={14} />
              </button>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
              {newNote.tags.map((tag) => (
                <TagChip key={tag} label={tag} onRemove={() => removeTag(tag, false)} />
              ))}
            </div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, paddingTop: 8 }}>
            <GhostBtn onClick={() => setIsCreating(false)}>Cancel</GhostBtn>
            <YellowBtn onClick={handleCreateNote} disabled={!newNote.title.trim() || !newNote.workspaceId || !hasPermission(newNote.workspaceId, 'notes', 'create')}>
              <Check size={13} /> Create
            </YellowBtn>
          </div>
        </div>
      </TTDialog>

      {/* ── Edit Note Dialog ──────────────────────────────────── */}
      <TTDialog open={!!editingNote} onClose={() => setEditingNote(null)} title="Edit Note">
        {editingNote && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            <div>
              <FieldLabel>Title</FieldLabel>
              <TTInput
                value={editingNote.title}
                onChange={(v) => setEditingNote({ ...editingNote, title: v })}
              />
            </div>
            <div>
              <FieldLabel>Type</FieldLabel>
              <Select
                value={editingNote.type || 'note'}
                onValueChange={(v) =>
                  setEditingNote({
                    ...editingNote,
                    type: v as 'note' | 'web-clip' | 'document' | 'voice' | 'ai-generated',
                  })
                }
              >
                <SelectTrigger style={{ height: 42, background: TT.inkRaised, borderColor: TT.inkBorder }}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent style={{ background: TT.inkDeep, border: `1px solid ${TT.inkBorder}` }}>
                  {Object.entries(noteTypeConfig).map(([key, cfg]) => {
                    const IconComponent = cfg.icon;
                    return (
                      <SelectItem key={key} value={key}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <IconComponent size={14} />
                          <span style={{ fontFamily: TT.fontMono, fontSize: 11 }}>{cfg.label}</span>
                        </div>
                      </SelectItem>
                    );
                  })}
                </SelectContent>
              </Select>
            </div>
            <div>
              <FieldLabel>Content</FieldLabel>
              <TTTextarea
                value={editingNote.content}
                onChange={(v) => setEditingNote({ ...editingNote, content: v })}
              />
            </div>
            <div>
              <FieldLabel>Tags</FieldLabel>
              <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                <TTInput
                  value={newTag}
                  onChange={setNewTag}
                  placeholder="Add tag..."
                  onKeyDown={(e) => e.key === 'Enter' && addTag(true)}
                />
                <button
                  onClick={() => addTag(true)}
                  style={{ height: 42, width: 42, background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, cursor: 'pointer', color: TT.inkMuted, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}
                >
                  <Plus size={14} />
                </button>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {editingNote.tags.map((tag) => (
                  <TagChip key={tag} label={tag} onRemove={() => removeTag(tag, true)} />
                ))}
              </div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, paddingTop: 8 }}>
              <GhostBtn onClick={() => setEditingNote(null)}>Cancel</GhostBtn>
              <YellowBtn onClick={handleUpdateNote} disabled={!editingNote.workspaceId || !hasPermission(editingNote.workspaceId, 'notes', 'update')}>
                <Check size={13} /> Save
              </YellowBtn>
            </div>
          </div>
        )}
      </TTDialog>

      {/* ── View Note Dialog ──────────────────────────────────── */}
      <TTDialog open={!!selectedNote} onClose={() => setSelectedNote(null)} title={selectedNote?.title ?? ''}>
        {selectedNote && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <span
                  style={{
                    fontFamily: TT.fontMono,
                    fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase',
                    padding: '2px 8px',
                    background: (noteTypeConfig[selectedNote.type] ?? noteTypeConfig.note).bg,
                    color: (noteTypeConfig[selectedNote.type] ?? noteTypeConfig.note).color,
                    border: `1px solid ${(noteTypeConfig[selectedNote.type] ?? noteTypeConfig.note).color}33`,
                    borderRadius: 2,
                  }}
                >
                  {(noteTypeConfig[selectedNote.type] ?? noteTypeConfig.note).label}
                </span>
                {selectedNote.source && (
                  <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMuted, padding: '2px 8px', border: `1px solid ${TT.inkBorder}`, borderRadius: 2 }}>
                    <Link2 size={9} /> Source
                  </span>
                )}
              </div>
              {selectedNote.confidence && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '4px 10px', background: 'rgba(245,230,66,0.08)', border: '1px solid rgba(245,230,66,0.2)', borderRadius: 3 }}>
                  <Brain size={11} color={TT.yolk} />
                  <span style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.yolk }}>
                    {Math.round(selectedNote.confidence * 100)}% confidence
                  </span>
                </div>
              )}
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
              {selectedNote.tags.map((tag) => (
                <span
                  key={tag}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '2px 7px', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, color: TT.inkMuted }}
                >
                  <Tag size={8} /> {tag}
                </span>
              ))}
            </div>

            {/* Word Count & Metadata */}
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '10px 12px', background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3 }}>
              {(selectedNote.word_count ?? 0) > 0 && (
                <div style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.inkMuted, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span>📝 {selectedNote.word_count} words</span>
                </div>
              )}
              {selectedNote.embedding && (
                <div style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.yolk, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <Sparkles size={10} /> Embedded
                </div>
              )}
              {(selectedNote.connections?.length || 0) > 0 && (
                <div style={{ fontFamily: TT.fontMono, fontSize: 10, color: '#60A5FA', display: 'flex', alignItems: 'center', gap: 4 }}>
                  <Link2 size={10} /> {selectedNote.connections?.length} linked
                </div>
              )}
            </div>

            {/* Connections Button */}
            <button
              onClick={() => setShowConnections(true)}
              style={{
                height: 38, width: '100%', padding: '0 14px',
                background: TT.inkRaised,
                border: `1px solid ${TT.inkBorder}`,
                borderRadius: 3,
                fontFamily: TT.fontMono,
                fontSize: 11, letterSpacing: '0.05em', textTransform: 'uppercase',
                color: TT.inkMuted,
                cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                transition: 'all 0.15s',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.borderColor = 'rgba(96,165,250,0.5)';
                (e.currentTarget as HTMLElement).style.color = '#60A5FA';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder;
                (e.currentTarget as HTMLElement).style.color = TT.inkMuted;
              }}
            >
              <Link2 size={11} /> {(selectedNote.connections?.length || 0) > 0 ? 'Edit' : 'Add'} Connections
            </button>

            <div style={{ background: 'rgba(245,230,66,0.04)', border: '1px solid rgba(245,230,66,0.15)', borderLeft: `3px solid ${TT.yolk}`, borderRadius: 3, padding: '12px 14px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                <Sparkles size={11} color={TT.yolk} />
                <span style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.yolk }}>
                  Connection Suggestions
                </span>
              </div>

              {suggestionsLoading ? (
                <p style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.inkMuted, letterSpacing: '0.02em' }}>
                  Finding related notes...
                </p>
              ) : connectionSuggestions.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {connectionSuggestions.map((suggestion) => (
                    <div
                      key={suggestion.id}
                      style={{
                        background: TT.inkRaised,
                        border: `1px solid ${TT.inkBorder}`,
                        borderRadius: 3,
                        padding: '10px 12px',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, marginBottom: 6 }}>
                        <div style={{ flex: 1 }}>
                          <p style={{ fontFamily: TT.fontMono, fontSize: 11.5, color: TT.snow, marginBottom: 4, letterSpacing: '0.02em' }}>
                            {suggestion.suggestedNote.title}
                          </p>
                          <p style={{ fontFamily: TT.fontBody, fontSize: 11, color: TT.inkMuted, lineHeight: 1.55 }}>
                            {suggestion.reason}
                          </p>
                        </div>
                        <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.yolk, whiteSpace: 'nowrap' }}>
                          {Math.round(suggestion.similarityScore * 100)}% match
                        </span>
                      </div>

                      {suggestion.suggestedNote.contentPreview && (
                        <p style={{ fontFamily: TT.fontBody, fontSize: 10.5, color: TT.inkDim, lineHeight: 1.45, marginBottom: 8 }}>
                          {suggestion.suggestedNote.contentPreview}
                        </p>
                      )}

                      {(suggestion.suggestedNote.tags || []).length > 0 && (
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 8 }}>
                          {suggestion.suggestedNote.tags.slice(0, 3).map((tag) => (
                            <TagChip key={`${suggestion.id}-${tag}`} label={tag} />
                          ))}
                        </div>
                      )}

                      <div style={{ display: 'flex', gap: 8 }}>
                        <button
                          onClick={() => handleConfirmConnectionSuggestion(suggestion)}
                          style={{
                            flex: 1,
                            height: 32,
                            background: '#60A5FA',
                            border: 'none',
                            borderRadius: 3,
                            color: 'white',
                            fontFamily: TT.fontMono,
                            fontSize: 10,
                            letterSpacing: '0.05em',
                            textTransform: 'uppercase',
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            gap: 5,
                          }}
                        >
                          <Check size={11} /> Confirm
                        </button>
                        <button
                          onClick={() => handleDismissConnectionSuggestion(suggestion)}
                          style={{
                            flex: 1,
                            height: 32,
                            background: 'transparent',
                            border: `1px solid ${TT.inkBorder}`,
                            borderRadius: 3,
                            color: TT.inkMuted,
                            fontFamily: TT.fontMono,
                            fontSize: 10,
                            letterSpacing: '0.05em',
                            textTransform: 'uppercase',
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            gap: 5,
                          }}
                        >
                          <X size={11} /> Dismiss
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.inkMuted, letterSpacing: '0.02em' }}>
                  No pending connection suggestions for this note right now.
                </p>
              )}
            </div>

            <div style={{ background: TT.inkDeep, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '14px 16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
                <div>
                  <div style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.yolk, marginBottom: 4 }}>
                    Knowledge Lineage
                  </div>
                  <div style={{ fontSize: 11, color: TT.inkMuted }}>
                    Every real change creates a snapshot. Restores add a new version instead of rewriting history.
                  </div>
                </div>
                <div style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.inkMuted }}>
                  {noteVersions.length} saved snapshot{noteVersions.length === 1 ? '' : 's'}
                </div>
              </div>

              {versionsLoading ? (
                <div style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.inkMuted }}>
                  Loading note history...
                </div>
              ) : noteVersions.length === 0 ? (
                <div style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.inkMuted }}>
                  No saved versions yet.
                </div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'minmax(240px, 280px) minmax(0, 1fr)', gap: 14, alignItems: 'start' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 520, overflowY: 'auto', paddingRight: 4 }}>
                    {noteVersions.map((version) => {
                      const active = selectedVersion?.id === version.id;
                      const isLatestVersion = latestVersion?.id === version.id;
                      const isRestoredVersion = version.changeReason === 'restored';
                      return (
                        <button
                          key={version.id}
                          onClick={() => setSelectedVersionId(version.id)}
                          style={{
                            width: '100%',
                            textAlign: 'left',
                            background: active ? 'rgba(245,230,66,0.08)' : TT.inkRaised,
                            border: `1px solid ${active ? 'rgba(245,230,66,0.35)' : TT.inkBorder}`,
                            borderRadius: 3,
                            padding: '11px 12px',
                            cursor: 'pointer',
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 7 }}>
                            <span style={{ fontFamily: TT.fontMono, fontSize: 10, color: active ? TT.yolk : TT.snow }}>
                              v{version.versionNumber}
                            </span>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                              <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMuted }}>
                                {getVersionChangeLabel(version)}
                              </span>
                              {isLatestVersion && (
                                <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, color: TT.yolk, border: '1px solid rgba(245,230,66,0.24)', borderRadius: 2, padding: '2px 5px', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
                                  Live
                                </span>
                              )}
                              {isRestoredVersion && (
                                <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, color: '#60A5FA', border: '1px solid rgba(96,165,250,0.22)', borderRadius: 2, padding: '2px 5px', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
                                  Restore
                                </span>
                              )}
                            </div>
                          </div>
                          <div style={{ fontSize: 10.5, color: TT.snow, marginBottom: 5, lineHeight: 1.4 }}>
                            {version.title || 'Untitled snapshot'}
                          </div>
                          <div style={{ fontSize: 10, color: TT.inkSubtle, marginBottom: 6, lineHeight: 1.45 }}>
                            {formatVersionSnapshotSummary(version)}
                          </div>
                          <div style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMuted }}>
                            {formatAbsoluteDate(version.createdAt)}
                          </div>
                        </button>
                      );
                    })}
                  </div>

                  {selectedVersion && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                          <span style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.snow }}>
                            Version v{selectedVersion.versionNumber} · {getVersionChangeLabel(selectedVersion)}
                          </span>
                          <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMuted }}>
                            {formatAbsoluteDate(selectedVersion.createdAt)}
                          </span>
                        </div>
                        {selectedVersionIsLatest ? (
                          <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMuted }}>
                            This snapshot already matches the live note.
                          </span>
                        ) : (
                          <button
                            onClick={() => handleRestoreVersion(selectedVersion)}
                            disabled={restoringVersionId === selectedVersion.id || !canUpdateSelectedNote}
                            style={{
                              height: 34,
                              padding: '0 12px',
                              background: restoringVersionId === selectedVersion.id ? TT.inkMid : 'rgba(245,230,66,0.08)',
                              border: `1px solid ${restoringVersionId === selectedVersion.id ? TT.inkBorder : 'rgba(245,230,66,0.3)'}`,
                              borderRadius: 3,
                              color: restoringVersionId === selectedVersion.id ? TT.inkMuted : TT.yolk,
                              fontFamily: TT.fontMono,
                              fontSize: 10,
                              letterSpacing: '0.05em',
                              textTransform: 'uppercase',
                              cursor: restoringVersionId === selectedVersion.id || !canUpdateSelectedNote ? 'default' : 'pointer',
                              opacity: canUpdateSelectedNote ? 1 : 0.45,
                            }}
                          >
                            {restoringVersionId === selectedVersion.id ? 'Restoring...' : 'Restore As New Version'}
                          </button>
                        )}
                      </div>

                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 10 }}>
                        <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '11px 12px' }}>
                          <div style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 6 }}>
                            Words
                          </div>
                          <div style={{ fontSize: 16, color: TT.snow }}>{selectedVersionStats?.wordCount || 0}</div>
                        </div>
                        <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '11px 12px' }}>
                          <div style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 6 }}>
                            Added
                          </div>
                          <div style={{ fontSize: 16, color: '#34D399' }}>+{selectedVersionStats?.wordsAdded || 0}</div>
                        </div>
                        <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '11px 12px' }}>
                          <div style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 6 }}>
                            Removed
                          </div>
                          <div style={{ fontSize: 16, color: '#FF7A7A' }}>-{selectedVersionStats?.wordsDeleted || 0}</div>
                        </div>
                        <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '11px 12px' }}>
                          <div style={{ fontFamily: TT.fontMono, fontSize: 8.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 6 }}>
                            Changed Fields
                          </div>
                          <div style={{ fontSize: 16, color: TT.snow }}>
                            {selectedVersionChangedFields.length || (selectedVersion.changeReason === 'created' ? 1 : 0)}
                          </div>
                        </div>
                      </div>

                      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.2fr) minmax(260px, 0.8fr)', gap: 12 }}>
                        <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '12px 14px' }}>
                          <div style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>
                            Change Summary
                          </div>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
                            {selectedVersion.changeReason === 'created' ? (
                              <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.yolk, border: '1px solid rgba(245,230,66,0.2)', borderRadius: 2, padding: '3px 6px' }}>
                                Initial snapshot
                              </span>
                            ) : selectedVersionChangedFields.length > 0 ? (
                              selectedVersionChangedFields.map((field) => (
                                <span key={`${selectedVersion.id}-${field}`} style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.snow, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, padding: '3px 6px' }}>
                                  {field}
                                </span>
                              ))
                            ) : (
                              <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMuted }}>
                                No field summary was saved for this version.
                              </span>
                            )}
                          </div>
                          <div style={{ display: 'grid', gap: 8 }}>
                            <div style={{ fontSize: 11, color: TT.inkSubtle, lineHeight: 1.6 }}>
                              {formatVersionSnapshotSummary(selectedVersion)}
                            </div>
                            {(selectedVersionTagDelta.added.length > 0 || selectedVersionTagDelta.removed.length > 0) && (
                              <div style={{ fontSize: 10.5, color: TT.inkSubtle, lineHeight: 1.6 }}>
                                Tags:
                                {selectedVersionTagDelta.added.length > 0 && (
                                  <span style={{ color: '#34D399' }}> +{selectedVersionTagDelta.added.join(', ')}</span>
                                )}
                                {selectedVersionTagDelta.removed.length > 0 && (
                                  <span style={{ color: '#FF7A7A' }}> -{selectedVersionTagDelta.removed.join(', ')}</span>
                                )}
                              </div>
                            )}
                            {(selectedVersionConnectionDelta.added.length > 0 || selectedVersionConnectionDelta.removed.length > 0) && (
                              <div style={{ fontSize: 10.5, color: TT.inkSubtle, lineHeight: 1.6 }}>
                                Connections:
                                {selectedVersionConnectionDelta.added.length > 0 && (
                                  <span style={{ color: '#34D399' }}> +{formatCountLabel(selectedVersionConnectionDelta.added.length, 'link')}</span>
                                )}
                                {selectedVersionConnectionDelta.removed.length > 0 && (
                                  <span style={{ color: '#FF7A7A' }}> -{formatCountLabel(selectedVersionConnectionDelta.removed.length, 'link')}</span>
                                )}
                              </div>
                            )}
                          </div>
                        </div>

                        <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '12px 14px' }}>
                          <div style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>
                            Timeline Context
                          </div>
                          <div style={{ display: 'grid', gap: 7 }}>
                            <div style={{ fontSize: 10.5, color: TT.inkSubtle, lineHeight: 1.6 }}>
                              Snapshot kind: <span style={{ color: TT.snow }}>{selectedVersionMetadata?.snapshotKind || 'revision'}</span>
                            </div>
                            {selectedVersionMetadata && selectedVersionMetadata.previousVersionNumber > 0 && (
                              <div style={{ fontSize: 10.5, color: TT.inkSubtle, lineHeight: 1.6 }}>
                                Based on: <span style={{ color: TT.snow }}>v{selectedVersionMetadata.previousVersionNumber}</span>
                              </div>
                            )}
                            <div style={{ fontSize: 10.5, color: TT.inkSubtle, lineHeight: 1.6 }}>
                              Word delta: <span style={{ color: selectedVersionStats && selectedVersionStats.wordDelta >= 0 ? '#34D399' : '#FF7A7A' }}>
                                {selectedVersionStats && selectedVersionStats.wordDelta >= 0 ? '+' : ''}{selectedVersionStats?.wordDelta || 0}
                              </span>
                            </div>
                            {selectedVersion.changeReason === 'restored' && selectedVersionMetadata && selectedVersionMetadata.restoredFromVersionNumber > 0 && (
                              <div style={{ fontSize: 10.5, color: TT.inkSubtle, lineHeight: 1.6 }}>
                                Restore source: <span style={{ color: TT.snow }}>v{selectedVersionMetadata.restoredFromVersionNumber}</span>
                              </div>
                            )}
                          </div>
                        </div>
                      </div>

                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 }}>
                        <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '12px 14px' }}>
                          <div style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>
                            Snapshot
                          </div>
                          <div style={{ fontSize: 12, color: TT.snow, marginBottom: 8 }}>{selectedVersion.title || 'Untitled snapshot'}</div>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 8 }}>
                            {selectedVersion.tags.length > 0 ? selectedVersion.tags.map((tag) => (
                              <span key={`version-tag-${selectedVersion.id}-${tag}`} style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMuted, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, padding: '2px 6px' }}>
                                {tag}
                              </span>
                            )) : (
                              <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMuted }}>
                                No tags saved in this snapshot.
                              </span>
                            )}
                          </div>
                          {selectedVersionIsLatest && (
                            <div style={{ fontSize: 10.5, color: TT.inkSubtle, lineHeight: 1.6, marginBottom: 8 }}>
                              This snapshot is the live note state right now.
                            </div>
                          )}
                          <div style={{ maxHeight: 220, overflowY: 'auto', fontSize: 11, lineHeight: 1.6, color: TT.inkSubtle, whiteSpace: 'pre-wrap' }}>
                            {selectedVersion.content || 'No content saved in this version.'}
                          </div>
                        </div>

                        <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '12px 14px' }}>
                          <div style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted, marginBottom: 8 }}>
                            Current
                          </div>
                          <div style={{ fontSize: 12, color: TT.snow, marginBottom: 8 }}>{selectedNote.title || 'Untitled note'}</div>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 8 }}>
                            {selectedNote.tags.length > 0 ? selectedNote.tags.map((tag) => (
                              <span key={`current-tag-${selectedNote.id}-${tag}`} style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMuted, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, padding: '2px 6px' }}>
                                {tag}
                              </span>
                            )) : (
                              <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkMuted }}>
                                No current tags.
                              </span>
                            )}
                          </div>
                          <div style={{ maxHeight: 220, overflowY: 'auto', fontSize: 11, lineHeight: 1.6, color: TT.inkSubtle, whiteSpace: 'pre-wrap' }}>
                            {selectedNote.content || 'No current content.'}
                          </div>
                        </div>
                      </div>

                      <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '12px 14px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
                          <div style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkMuted }}>
                            Inline Diff
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                            <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, color: '#34D399', border: '1px solid rgba(52,211,153,0.2)', borderRadius: 2, padding: '2px 5px' }}>
                              +{selectedVersionStats?.wordsAdded || 0}
                            </span>
                            <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, color: '#FF7A7A', border: '1px solid rgba(255,122,122,0.2)', borderRadius: 2, padding: '2px 5px' }}>
                              -{selectedVersionStats?.wordsDeleted || 0}
                            </span>
                            <span style={{ fontFamily: TT.fontMono, fontSize: 8.5, color: TT.inkMuted, border: `1px solid ${TT.inkBorder}`, borderRadius: 2, padding: '2px 5px' }}>
                              {formatCountLabel(selectedVersionStats?.changedSegments || 0, 'change')}
                            </span>
                          </div>
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, lineHeight: 1.9, maxHeight: 220, overflowY: 'auto' }}>
                          {selectedVersion.diffSegments.length > 0 ? selectedVersion.diffSegments.map((segment, index) => (
                            <span
                              key={`${selectedVersion.id}-segment-${index}`}
                              style={{
                                padding: segment.type === 'unchanged' ? '0' : '1px 3px',
                                borderRadius: 2,
                                background:
                                  segment.type === 'added'
                                    ? 'rgba(52,211,153,0.16)'
                                    : segment.type === 'deleted'
                                      ? 'rgba(255,69,69,0.16)'
                                      : 'transparent',
                                color:
                                  segment.type === 'added'
                                    ? '#34D399'
                                    : segment.type === 'deleted'
                                      ? '#FF7A7A'
                                      : TT.inkSubtle,
                                textDecoration: segment.type === 'deleted' ? 'line-through' : 'none',
                                whiteSpace: 'pre-wrap',
                              }}
                            >
                              {segment.text}
                            </span>
                          )) : (
                            <span style={{ fontSize: 10.5, color: TT.inkMuted }}>
                              No diff segments were saved for this version.
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {selectedNoteWorkspace && (
              <div style={{ background: 'rgba(96,165,250,0.05)', border: '1px solid rgba(96,165,250,0.15)', borderLeft: '3px solid #60A5FA', borderRadius: 3, padding: '12px 14px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                  <Globe size={11} color="#60A5FA" />
                  <span style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: '#60A5FA' }}>
                    Workspace
                  </span>
                </div>
                <p style={{ fontFamily: TT.fontMono, fontSize: 12, color: TT.snow, marginBottom: 6 }}>
                  {selectedNoteWorkspace.name}
                </p>
                {selectedNoteWorkspace.description && (
                  <p style={{ fontFamily: TT.fontBody, fontSize: 11.5, color: TT.inkMuted, lineHeight: 1.5, marginBottom: 8 }}>
                    {selectedNoteWorkspace.description}
                  </p>
                )}
              </div>
            )}

            {selectedNoteWorkspaceMembers.length > 0 && (
              <div style={{ background: 'rgba(167,139,250,0.05)', border: '1px solid rgba(167,139,250,0.15)', borderLeft: '3px solid #A78BFA', borderRadius: 3, padding: '12px 14px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                  <Users size={11} color="#A78BFA" />
                  <span style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: '#A78BFA' }}>
                    Team Members ({selectedNoteWorkspaceMembers.length})
                  </span>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {selectedNoteWorkspaceMembers.map((member: any) => {
                    const isCreator = member.user_id === selectedNote.userId;
                    const displayName = member.full_name || member.email || 'Unknown';
                    const initial = (member.full_name?.charAt(0) || member.email?.charAt(0) || 'U').toUpperCase();
                    return (
                      <div key={member.user_id} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <div style={{ width: 28, height: 28, borderRadius: 3, background: isCreator ? TT.yolk : TT.inkMid, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: TT.fontDisplay, fontSize: 12, color: isCreator ? TT.inkBlack : TT.snow }}>
                          {initial}
                        </div>
                        <div style={{ flex: 1 }}>
                          <p style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.snow, letterSpacing: '0.02em' }}>
                            {displayName}
                            {isCreator && <span style={{ color: TT.yolk, marginLeft: 6 }}>(creator)</span>}
                          </p>
                          <p style={{ fontFamily: TT.fontMono, fontSize: 8.5, color: TT.inkMuted, letterSpacing: '0.02em', textTransform: 'uppercase' }}>
                            {member.role}
                          </p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            <div style={{ background: TT.inkRaised, border: `1px solid ${TT.inkBorder}`, borderRadius: 3, padding: '14px 16px' }}>
              <p style={{ fontFamily: TT.fontBody, fontSize: 13, lineHeight: 1.7, color: TT.inkSubtle, whiteSpace: 'pre-wrap' }}>
                {selectedNote.content}
              </p>
            </div>

            {selectedNote.summary && (
              <div style={{ background: 'rgba(245,230,66,0.05)', border: '1px solid rgba(245,230,66,0.15)', borderLeft: `3px solid ${TT.yolk}`, borderRadius: 3, padding: '12px 14px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                  <Sparkles size={11} color={TT.yolk} />
                  <span style={{ fontFamily: TT.fontMono, fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.yolk }}>
                    AI Summary
                  </span>
                </div>
                <p style={{ fontFamily: TT.fontBody, fontSize: 12.5, color: TT.inkMuted, lineHeight: 1.6 }}>
                  {selectedNote.summary}
                </p>
              </div>
            )}

            {/* FIX: use safeFromNow instead of formatDistanceToNow */}
            <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: `1px solid ${TT.inkBorder}`, paddingTop: 12 }}>
              {[
                { label: 'Created', date: selectedNote.createdAt },
                { label: 'Updated', date: selectedNote.updatedAt },
              ].map(({ label, date }) => (
                <span key={label} style={{ fontFamily: TT.fontMono, fontSize: 9.5, color: TT.inkMid, letterSpacing: '0.04em' }}>
                  {label}: {safeFromNow(date)}
                </span>
              ))}
            </div>
          </div>
        )}
      </TTDialog>

      {/* ── Connections Editor Modal ──────────────────────────── */}
      <TTDialog open={showConnections} onClose={() => setShowConnections(false)} title="Manage Connections">
        {selectedNote && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxHeight: '60vh', overflowY: 'auto' }}>
            <p style={{ fontFamily: TT.fontMono, fontSize: 11, color: TT.inkMuted, letterSpacing: '0.02em' }}>
              Select notes to link with "{selectedNote.title}". These connections help you discover related information.
            </p>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {notes.map((note) => {
                if (note.id === selectedNote.id) return null; // Don't allow self-linking
                const isConnected = (selectedNote.connections || []).includes(note.id);

                return (
                  <label
                    key={note.id}
                    style={{
                      display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 12px',
                      background: isConnected ? 'rgba(96,165,250,0.08)' : TT.inkRaised,
                      border: `1px solid ${isConnected ? 'rgba(96,165,250,0.4)' : TT.inkBorder}`,
                      borderRadius: 3,
                      cursor: 'pointer',
                      transition: 'all 0.15s',
                    }}
                    onMouseEnter={(e) => {
                      (e.currentTarget as HTMLElement).style.background = isConnected ? 'rgba(96,165,250,0.12)' : TT.inkMid;
                      (e.currentTarget as HTMLElement).style.borderColor = isConnected ? 'rgba(96,165,250,0.6)' : TT.inkBorder;
                    }}
                    onMouseLeave={(e) => {
                      (e.currentTarget as HTMLElement).style.background = isConnected ? 'rgba(96,165,250,0.08)' : TT.inkRaised;
                      (e.currentTarget as HTMLElement).style.borderColor = isConnected ? 'rgba(96,165,250,0.4)' : TT.inkBorder;
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={isConnected}
                      onChange={(e) => {
                        const newConnections = e.target.checked
                          ? [...(selectedNote.connections || []), note.id]
                          : (selectedNote.connections || []).filter((id) => id !== note.id);
                        setSelectedNote({ ...selectedNote, connections: newConnections });
                      }}
                      style={{ marginTop: 3, cursor: 'pointer', accentColor: '#60A5FA' }}
                    />
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                        <span
                          style={{
                            fontFamily: TT.fontMono,
                            fontSize: 8, letterSpacing: '0.08em', textTransform: 'uppercase',
                            padding: '1px 6px',
                            background: (noteTypeConfig[note.type] ?? noteTypeConfig.note).bg,
                            color: (noteTypeConfig[note.type] ?? noteTypeConfig.note).color,
                            border: `1px solid ${(noteTypeConfig[note.type] ?? noteTypeConfig.note).color}33`,
                            borderRadius: 2,
                          }}
                        >
                          {(noteTypeConfig[note.type] ?? noteTypeConfig.note).label}
                        </span>
                        <span style={{ fontFamily: TT.fontMono, fontSize: 12, color: TT.snow, flex: 1 }}>
                          {note.title}
                        </span>
                      </div>
                      <p style={{ fontFamily: TT.fontBody, fontSize: 10.5, color: TT.inkMuted, lineHeight: 1.4 }}>
                        {note.content.length > 80 ? `${note.content.substring(0, 80)}...` : note.content}
                      </p>
                      {(note.word_count ?? 0) > 0 && (
                        <span style={{ fontFamily: TT.fontMono, fontSize: 9, color: TT.inkDim, marginTop: 4 }}>
                          📝 {note.word_count} words
                        </span>
                      )}
                    </div>
                  </label>
                );
              })}
            </div>

            {notes.length === 1 && (
              <div style={{ padding: '14px 12px', background: 'rgba(167,139,250,0.05)', border: '1px solid rgba(167,139,250,0.15)', borderRadius: 3 }}>
                <p style={{ fontFamily: TT.fontMono, fontSize: 10, color: TT.inkMuted, letterSpacing: '0.02em' }}>
                  Create more notes to establish connections between them.
                </p>
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, paddingTop: 8, borderTop: `1px solid ${TT.inkBorder}` }}>
              <button
                onClick={() => handleUpdateConnections()}
                disabled={!canUpdateSelectedNote}
                style={{
                  flex: 1, height: 36, padding: '0 14px',
                  background: canUpdateSelectedNote ? '#60A5FA' : TT.inkMid,
                  border: 'none',
                  borderRadius: 3,
                  fontFamily: TT.fontMono,
                  fontSize: 10.5, letterSpacing: '0.05em', textTransform: 'uppercase', fontWeight: 500,
                  color: canUpdateSelectedNote ? 'white' : TT.inkMuted,
                  cursor: canUpdateSelectedNote ? 'pointer' : 'not-allowed',
                  transition: 'all 0.15s',
                }}
                onMouseEnter={(e) => {
                  if (!canUpdateSelectedNote) return;
                  (e.currentTarget as HTMLElement).style.background = '#3b82f6';
                  (e.currentTarget as HTMLElement).style.boxShadow = '0 0 12px rgba(96,165,250,0.4)';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.background = canUpdateSelectedNote ? '#60A5FA' : TT.inkMid;
                  (e.currentTarget as HTMLElement).style.boxShadow = 'none';
                }}
              >
                Save Connections
              </button>
              <button
                onClick={() => setShowConnections(false)}
                style={{
                  flex: 1, height: 36, padding: '0 14px',
                  background: TT.inkRaised,
                  border: `1px solid ${TT.inkBorder}`,
                  borderRadius: 3,
                  fontFamily: TT.fontMono,
                  fontSize: 10.5, letterSpacing: '0.05em', textTransform: 'uppercase',
                  color: TT.inkMuted,
                  cursor: 'pointer',
                  transition: 'all 0.15s',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder;
                  (e.currentTarget as HTMLElement).style.color = TT.snow;
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.borderColor = TT.inkBorder;
                  (e.currentTarget as HTMLElement).style.color = TT.inkMuted;
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </TTDialog>
    </div>
  );
}
