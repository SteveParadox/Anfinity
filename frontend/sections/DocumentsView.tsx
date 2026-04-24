/**
 * DocumentsView — lists all documents in the current workspace
 *
 * Improvements over v1
 * ─────────────────────────────────────────────────────────────
 * • Polling no longer flashes: initial load uses isLoading; background
 *   refreshes use a separate isRefreshing flag so the list stays visible.
 * • Dead `Eye` import removed.
 * • `window.confirm()` replaced with an inline "confirm delete" row state.
 * • Delete / retry failures surface a dismissible inline banner instead of
 *   being silently swallowed.
 * • Retry does an optimistic status patch before the re-fetch.
 * • `loadDocuments` extracted to a `useCallback` ref so it's reusable
 *   without duplication inside `handleRetry`.
 * • `workspaceId` is narrowed from `string | undefined` before being
 *   forwarded to child components.
 * • `StatusBadge` has an explicit `unknown` fallback (grey, neutral icon).
 * • All icon-only buttons carry `aria-label` alongside `title`.
 * • Manual refresh button added to the header.
 */

import { useState, useEffect, useContext, useCallback, useDeferredValue, useMemo } from 'react';
import {
  FileText,
  Trash2,
  RefreshCw,
  AlertCircle,
  Check,
  Loader2,
  HelpCircle,
  RotateCcw,
  X,
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { api } from '@/lib/api';
import { AuthContext } from '@/contexts/AuthContext';
import { useDocumentIngestion } from '@/hooks/Ingestionhooks';
import type { Document as DocumentType } from '@/types';
import { formatDistanceToNow } from 'date-fns';

// ─── Design tokens ─────────────────────────────────────────────────────────────

const TT = {
  inkBlack: '#0A0A0A',
  inkDeep: '#111111',
  inkRaised: '#1A1A1A',
  inkBorder: '#252525',
  inkMid: '#3A3A3A',
  inkMuted: '#5A5A5A',
  inkSubtle: '#888888',
  snow: '#F5F5F5',
  yolk: '#F5E642',
  errorBg: 'rgba(255, 69, 69, 0.10)',
  errorBorder: 'rgba(255, 69, 69, 0.30)',
  errorText: '#FF4545',
  successBg: 'rgba(76, 175, 80, 0.10)',
  successBorder: 'rgba(76, 175, 80, 0.30)',
  successText: '#4CAF50',
  warnBg: 'rgba(245, 230, 66, 0.10)',
  warnBorder: 'rgba(245, 230, 66, 0.30)',
  warnText: '#F5E642',
  neutralBg: 'rgba(136, 136, 136, 0.10)',
  neutralBorder: 'rgba(136, 136, 136, 0.30)',
  neutralText: '#888888',
} as const;

// ─── Status badge ───────────────────────────────────────────────────────────────

interface StatusBadgeProps {
  status: string;
  error?: string | null;
}

type StatusConfig = {
  bg: string;
  border: string;
  text: string;
  icon: React.ReactNode;
};

const STATUS_CONFIG: Record<string, StatusConfig> = {
  pending: {
    bg: TT.warnBg,
    border: TT.warnBorder,
    text: TT.warnText,
    icon: <Loader2 size={14} className="animate-spin" />,
  },
  processing: {
    bg: TT.warnBg,
    border: TT.warnBorder,
    text: TT.warnText,
    icon: <Loader2 size={14} className="animate-spin" />,
  },
  indexed: {
    bg: TT.successBg,
    border: TT.successBorder,
    text: TT.successText,
    icon: <Check size={14} />,
  },
  failed: {
    bg: TT.errorBg,
    border: TT.errorBorder,
    text: TT.errorText,
    icon: <AlertCircle size={14} />,
  },
  // Explicit fallback for unrecognised statuses — clearly distinct from
  // pending so it doesn't mislead the user into thinking work is in progress.
  unknown: {
    bg: TT.neutralBg,
    border: TT.neutralBorder,
    text: TT.neutralText,
    icon: <HelpCircle size={14} />,
  },
};

function StatusBadge({ status, error }: StatusBadgeProps) {
  const config = STATUS_CONFIG[status] ?? STATUS_CONFIG.unknown;
  const label = status.charAt(0).toUpperCase() + status.slice(1);

  return (
    <div
      role="status"
      aria-label={`Document status: ${label}${error ? ` — ${error}` : ''}`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '6px',
        padding: '4px 8px',
        background: config.bg,
        border: `1px solid ${config.border}`,
        borderRadius: '4px',
        fontSize: '11px',
        fontWeight: 600,
        textTransform: 'uppercase',
        color: config.text,
        whiteSpace: 'nowrap',
      }}
    >
      {config.icon}
      {label}
      {error && (
        <span title={error} aria-label={`Error: ${error}`} style={{ lineHeight: 1 }}>
          !
        </span>
      )}
    </div>
  );
}

// ─── Inline error banner ────────────────────────────────────────────────────────

interface InlineErrorProps {
  message: string;
  onDismiss: () => void;
}

function InlineError({ message, onDismiss }: InlineErrorProps) {
  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: 'auto' }}
      exit={{ opacity: 0, height: 0 }}
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '8px',
        padding: '8px 12px',
        background: TT.errorBg,
        border: `1px solid ${TT.errorBorder}`,
        borderRadius: '4px',
        fontSize: '12px',
        color: TT.errorText,
        overflow: 'hidden',
      }}
    >
      <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <AlertCircle size={12} />
        {message}
      </span>
      <button
        onClick={onDismiss}
        aria-label="Dismiss error"
        style={{
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          color: TT.errorText,
          padding: '2px',
          lineHeight: 0,
        }}
      >
        <X size={12} />
      </button>
    </motion.div>
  );
}

// ─── Document row ───────────────────────────────────────────────────────────────

interface DocumentRowProps {
  doc: DocumentType;
  workspaceId: string;
  onDelete: (id: string) => void;
  onRetryOptimistic: (id: string) => void;
  onReload: () => Promise<void>;
  canMutate: boolean;
  selected: boolean;
  onSelect: (id: string) => void;
}

function DocumentRow({
  doc,
  workspaceId,
  onDelete,
  onRetryOptimistic,
  onReload,
  canMutate,
  selected,
  onSelect,
}: DocumentRowProps) {
  const ingestion = useDocumentIngestion(doc.id, workspaceId);

  // Inline confirmation state replaces window.confirm()
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [rowError, setRowError] = useState<string | null>(null);

  const handleDeleteClick = useCallback(() => {
    // First click arms the confirmation UI
    setConfirmingDelete(true);
  }, []);

  const handleDeleteConfirm = useCallback(async () => {
    setDeleting(true);
    setConfirmingDelete(false);
    try {
      await api.deleteDocument(doc.id);
      onDelete(doc.id);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Delete failed';
      setRowError(msg);
    } finally {
      setDeleting(false);
    }
  }, [doc.id, onDelete]);

  const handleDeleteCancel = useCallback(() => {
    setConfirmingDelete(false);
  }, []);

  const handleRetry = useCallback(async () => {
    // Optimistically flip the status before the network round-trip so the
    // user gets immediate feedback.
    onRetryOptimistic(doc.id);
    try {
      await api.retryIngestion(doc.id);
      await onReload();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Retry failed';
      setRowError(msg);
    }
  }, [doc.id, onRetryOptimistic, onReload]);

  const effectiveStatus = ingestion.status ?? doc.status;
  const isFailed = effectiveStatus === 'failed';
  const isProcessing =
    ingestion.status != null &&
    ['started', 'downloading', 'parsing', 'chunking', 'embedding'].includes(ingestion.status);

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}
    >
      {/* Main row */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => onSelect(doc.id)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onSelect(doc.id);
          }
        }}
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr auto auto auto auto',
          gap: '16px',
          alignItems: 'center',
          padding: '12px 16px',
          background: selected ? 'rgba(245,230,66,0.05)' : TT.inkRaised,
          border: `1px solid ${confirmingDelete ? TT.errorBorder : selected ? 'rgba(245,230,66,0.28)' : TT.inkBorder}`,
          borderLeft: `3px solid ${selected ? TT.yolk : 'transparent'}`,
          borderRadius: '6px',
          fontSize: '13px',
          transition: 'border-color 0.2s',
          cursor: 'pointer',
        }}
      >
        {/* Title + metadata */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
            <FileText size={14} style={{ color: TT.inkSubtle, flexShrink: 0 }} />
            <span
              style={{
                fontWeight: 500,
                color: TT.snow,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
              title={doc.title}
            >
              {doc.title}
            </span>
          </div>
          <div style={{ fontSize: '11px', color: TT.inkSubtle }}>
            Created {formatDistanceToNow(new Date(doc.createdAt))} ago
          </div>
        </div>

        {/* Stats */}
        <div style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
          <div style={{ color: TT.snow }}>
            {doc.chunkCount ?? 0} <span style={{ color: TT.inkSubtle }}>chunks</span>
          </div>
          <div style={{ fontSize: '11px', color: TT.inkSubtle }}>
            {doc.tokenCount ?? 0} tokens
          </div>
        </div>

        {/* Progress bar */}
        <div style={{ minWidth: '100px' }}>
          {isProcessing && ingestion.progress?.percentage != null && (
            <>
              <div
                role="progressbar"
                aria-valuenow={ingestion.progress.percentage}
                aria-valuemin={0}
                aria-valuemax={100}
                style={{
                  height: '4px',
                  background: TT.inkMid,
                  borderRadius: '2px',
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    height: '100%',
                    background: TT.yolk,
                    width: `${ingestion.progress.percentage}%`,
                    transition: 'width 0.3s ease',
                  }}
                />
              </div>
              <div style={{ fontSize: '10px', color: TT.inkSubtle, marginTop: '2px', textAlign: 'right' }}>
                {ingestion.progress.percentage}%
              </div>
            </>
          )}
        </div>

        {/* Status badge */}
        <StatusBadge status={effectiveStatus} error={ingestion.error} />

        {/* Actions */}
        <div style={{ display: 'flex', gap: '4px', justifyContent: 'flex-end' }}>
          {isFailed && (
            <IconButton
              onClick={handleRetry}
              disabled={!canMutate}
              title="Retry processing"
              aria-label="Retry processing this document"
              color={TT.yolk}
            >
              <RotateCcw size={14} />
            </IconButton>
          )}

          {confirmingDelete ? (
            <>
              <IconButton
                onClick={handleDeleteConfirm}
                title="Confirm delete"
                aria-label="Confirm delete"
                color={TT.errorText}
              >
                <Check size={14} />
              </IconButton>
              <IconButton
                onClick={handleDeleteCancel}
                title="Cancel delete"
                aria-label="Cancel delete"
                color={TT.inkSubtle}
              >
                <X size={14} />
              </IconButton>
            </>
          ) : (
            <IconButton
              onClick={handleDeleteClick}
              disabled={deleting || !canMutate}
              title="Delete document"
              aria-label="Delete this document"
              color={TT.errorText}
              loading={deleting}
            >
              <Trash2 size={14} />
            </IconButton>
          )}
        </div>
      </div>

      {/* Row-level error — appears below the row, not above, so layout doesn't shift */}
      <AnimatePresence>
        {rowError && (
          <InlineError message={rowError} onDismiss={() => setRowError(null)} />
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// ─── Shared icon button primitive ──────────────────────────────────────────────

interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  color?: string;
  loading?: boolean;
  children: React.ReactNode;
}

function IconButton({ color = TT.inkSubtle, loading = false, children, style, ...rest }: IconButtonProps) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      style={{
        background: 'none',
        border: 'none',
        cursor: rest.disabled || loading ? 'not-allowed' : 'pointer',
        color,
        opacity: rest.disabled || loading ? 0.5 : 1,
        padding: '4px 8px',
        borderRadius: '4px',
        lineHeight: 0,
        transition: 'background 0.15s, color 0.15s',
        ...style,
      }}
      onMouseEnter={(e) => {
        if (!rest.disabled && !loading) e.currentTarget.style.background = TT.inkBorder;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'none';
      }}
    >
      {loading ? <Loader2 size={14} className="animate-spin" /> : children}
    </button>
  );
}

// ─── Main view ─────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 10_000; // Background refresh cadence

export function DocumentsView() {
  const [documents, setDocuments] = useState<DocumentType[]>([]);
  const [isLoading, setIsLoading] = useState(true);    // True only on the first load
  const [isRefreshing, setIsRefreshing] = useState(false); // True on background polls
  const [pageError, setPageError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | DocumentType['status']>('all');
  const [sortMode, setSortMode] = useState<'recent' | 'title' | 'largest'>('recent');
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);

  const deferredSearchQuery = useDeferredValue(searchQuery.trim().toLowerCase());

  const authContext = useContext(AuthContext);
  const workspaceId = authContext?.currentWorkspaceId;
  const hasPermission = authContext?.hasPermission ?? (() => false);
  const canViewDocuments = Boolean(workspaceId && hasPermission(workspaceId, 'documents', 'view'));
  const canMutateDocuments = Boolean(workspaceId && (hasPermission(workspaceId, 'documents', 'delete') || hasPermission(workspaceId, 'documents', 'update')));

  // ── Data fetching ───────────────────────────────────────────────────────────

  const loadDocuments = useCallback(
    async (opts: { silent?: boolean } = {}) => {
      if (!workspaceId || !canViewDocuments) return;

      // Distinguish first-load (shows spinner) from background refresh (no spinner).
      if (!opts.silent) setIsLoading(true);
      else setIsRefreshing(true);

      setPageError(null);

      try {
        const response = await api.listDocuments(workspaceId, {
          page: 1,
          page_size: 50,
          sort_by: 'created_at',
          sort_order: 'desc',
        });
        setDocuments(response.items ?? []);
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to load documents';
        console.error('Failed to load documents:', err);
        setPageError(msg);
      } finally {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    },
    [canViewDocuments, workspaceId],
  );

  // Initial load + background polling
  useEffect(() => {
    if (!workspaceId) {
      setIsLoading(false);
      return;
    }

    if (!canViewDocuments) {
      setDocuments([]);
      setIsLoading(false);
      return;
    }

    loadDocuments({ silent: false });

    const interval = setInterval(() => loadDocuments({ silent: true }), POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [canViewDocuments, workspaceId, loadDocuments]);

  // ── Row callbacks ───────────────────────────────────────────────────────────

  const handleDelete = useCallback((id: string) => {
    setDocuments((prev) => prev.filter((doc) => doc.id !== id));
  }, []);

  /**
   * Optimistically flip a document to `pending` so the user sees immediate
   * feedback while the retry request is in flight.
   */
  const handleRetryOptimistic = useCallback((id: string) => {
    setDocuments((prev) =>
      prev.map((doc) => (doc.id === id ? { ...doc, status: 'pending' } : doc)),
    );
  }, []);

  /**
   * Stable reference to a silent reload, forwarded to DocumentRow so it can
   * refresh the list after a retry without duplicating the fetch logic.
   */
  const handleReload = useCallback(() => loadDocuments({ silent: true }), [loadDocuments]);

  useEffect(() => {
    setSelectedDocumentId((current) => (current && documents.some((doc) => doc.id === current) ? current : documents[0]?.id ?? null));
  }, [documents]);

  const documentStats = useMemo(() => {
    return {
      total: documents.length,
      indexed: documents.filter((doc) => doc.status === 'indexed').length,
      processing: documents.filter((doc) => doc.status === 'processing' || doc.status === 'pending').length,
      failed: documents.filter((doc) => doc.status === 'failed').length,
    };
  }, [documents]);

  const filteredDocuments = useMemo(() => {
    const nextDocuments = documents.filter((doc) => {
      const matchesStatus = statusFilter === 'all' || doc.status === statusFilter;
      const matchesQuery =
        !deferredSearchQuery ||
        [doc.title, doc.sourceType, doc.status]
          .join(' ')
          .toLowerCase()
          .includes(deferredSearchQuery);

      return matchesStatus && matchesQuery;
    });

    nextDocuments.sort((left, right) => {
      if (sortMode === 'title') {
        return left.title.localeCompare(right.title);
      }

      if (sortMode === 'largest') {
        return (right.tokenCount ?? 0) - (left.tokenCount ?? 0);
      }

      return new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime();
    });

    return nextDocuments;
  }, [deferredSearchQuery, documents, sortMode, statusFilter]);

  const selectedDocument =
    documents.find((doc) => doc.id === selectedDocumentId) ??
    filteredDocuments[0] ??
    null;

  // ── Guard: no workspace ─────────────────────────────────────────────────────

  if (!workspaceId) {
    return (
      <div style={{ padding: '24px', textAlign: 'center', color: TT.inkSubtle }}>
        Please select a workspace to view documents.
      </div>
    );
  }

  if (!canViewDocuments) {
    return (
      <div style={{ padding: '24px', textAlign: 'center', color: TT.inkSubtle }}>
        Your current role does not allow viewing documents in this workspace.
      </div>
    );
  }

  // ── Guard: initial loading ─────────────────────────────────────────────────

  if (isLoading) {
    return (
      <div style={{ padding: '24px', textAlign: 'center', color: TT.inkSubtle }}>
        <Loader2 size={20} className="animate-spin" style={{ margin: '0 auto 12px', display: 'block' }} />
        Loading documents…
      </div>
    );
  }

  // ── Guard: hard error (no documents to show) ────────────────────────────────

  if (pageError && documents.length === 0) {
    return (
      <div style={{ padding: '24px' }}>
        <div
          style={{
            padding: '16px',
            background: TT.errorBg,
            border: `1px solid ${TT.errorBorder}`,
            borderRadius: '8px',
            color: TT.errorText,
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
          }}
        >
          <AlertCircle size={16} style={{ flexShrink: 0 }} />
          <span>{pageError}</span>
          <button
            onClick={() => loadDocuments({ silent: false })}
            style={{
              marginLeft: 'auto',
              background: TT.errorBorder,
              border: 'none',
              borderRadius: '4px',
              color: TT.errorText,
              padding: '4px 10px',
              cursor: 'pointer',
              fontSize: '12px',
            }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  // ── Main render ─────────────────────────────────────────────────────────────

  return (
    <div style={{ padding: '24px' }}>
      {/* Header */}
      <div
        style={{
          marginBottom: '24px',
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: '16px',
        }}
      >
        <div>
          <h1 style={{ fontSize: '28px', fontWeight: 700, color: TT.snow, marginBottom: '6px' }}>
            Documents
          </h1>
          <p style={{ color: TT.inkSubtle, fontSize: '14px' }}>
            {filteredDocuments.length} visible of {documents.length} document{documents.length !== 1 ? 's' : ''} in this workspace
          </p>
        </div>

        {/* Manual refresh */}
        <button
          onClick={() => loadDocuments({ silent: false })}
          disabled={isRefreshing}
          title="Refresh document list"
          aria-label="Refresh document list"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            background: TT.inkRaised,
            border: `1px solid ${TT.inkBorder}`,
            borderRadius: '6px',
            color: isRefreshing ? TT.inkSubtle : TT.snow,
            padding: '8px 14px',
            cursor: isRefreshing ? 'default' : 'pointer',
            fontSize: '13px',
            transition: 'border-color 0.2s',
            flexShrink: 0,
          }}
          onMouseEnter={(e) => { if (!isRefreshing) e.currentTarget.style.borderColor = TT.inkMid; }}
          onMouseLeave={(e) => { e.currentTarget.style.borderColor = TT.inkBorder; }}
        >
          <RefreshCw
            size={14}
            style={{
              animation: isRefreshing ? 'spin 1s linear infinite' : 'none',
            }}
          />
          {isRefreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {/* Soft error while documents are still shown (e.g. a background poll failed) */}
      <AnimatePresence>
        {pageError && documents.length > 0 && (
          <InlineError
            message={`Background refresh failed: ${pageError}`}
            onDismiss={() => setPageError(null)}
          />
        )}
      </AnimatePresence>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '10px', marginBottom: '16px' }}>
        {[
          { label: 'Total', value: documentStats.total, helper: 'all documents' },
          { label: 'Indexed', value: documentStats.indexed, helper: 'search-ready' },
          { label: 'Processing', value: documentStats.processing, helper: 'still running' },
          { label: 'Failed', value: documentStats.failed, helper: 'need attention' },
        ].map(({ label, value, helper }) => (
          <div
            key={label}
            style={{
              background: TT.inkRaised,
              border: `1px solid ${TT.inkBorder}`,
              borderLeft: `3px solid ${TT.yolk}`,
              borderRadius: '6px',
              padding: '14px 16px',
            }}
          >
            <div style={{ fontSize: '10px', letterSpacing: '0.08em', textTransform: 'uppercase', color: TT.inkSubtle, marginBottom: '6px' }}>
              {label}
            </div>
            <div style={{ fontSize: '26px', fontWeight: 700, color: TT.snow, lineHeight: 1 }}>{value}</div>
            <div style={{ fontSize: '11px', color: TT.inkMuted, marginTop: '6px' }}>{helper}</div>
          </div>
        ))}
      </div>

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '10px',
          alignItems: 'center',
          marginBottom: '16px',
          padding: '12px',
          background: TT.inkRaised,
          border: `1px solid ${TT.inkBorder}`,
          borderRadius: '8px',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            background: TT.inkBlack,
            border: `1px solid ${TT.inkBorder}`,
            borderRadius: '6px',
            padding: '0 10px',
            height: '38px',
            minWidth: '240px',
          }}
        >
          <FileText size={13} color={TT.inkMuted} />
          <input
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="Search documents"
            aria-label="Search documents"
            style={{
              flex: 1,
              height: '100%',
              background: 'transparent',
              border: 'none',
              color: TT.snow,
              outline: 'none',
              fontSize: '13px',
            }}
          />
        </div>

        {(['all', 'indexed', 'processing', 'pending', 'failed'] as const).map((status) => (
          <button
            key={status}
            type="button"
            onClick={() => setStatusFilter(status)}
            aria-pressed={statusFilter === status}
            style={{
              borderRadius: '999px',
              border: `1px solid ${statusFilter === status ? 'rgba(245,230,66,0.28)' : TT.inkBorder}`,
              background: statusFilter === status ? 'rgba(245,230,66,0.08)' : TT.inkBlack,
              color: statusFilter === status ? TT.yolk : TT.inkMuted,
              padding: '6px 10px',
              fontSize: '10px',
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              cursor: 'pointer',
            }}
          >
            {status}
          </button>
        ))}

        {(['recent', 'title', 'largest'] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            onClick={() => setSortMode(mode)}
            aria-pressed={sortMode === mode}
            style={{
              borderRadius: '999px',
              border: `1px solid ${sortMode === mode ? 'rgba(245,245,245,0.14)' : TT.inkBorder}`,
              background: sortMode === mode ? TT.inkBorder : TT.inkBlack,
              color: sortMode === mode ? TT.snow : TT.inkMuted,
              padding: '6px 10px',
              fontSize: '10px',
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              cursor: 'pointer',
            }}
          >
            {mode}
          </button>
        ))}
      </div>

      {selectedDocument ? (
        <div
          style={{
            marginBottom: '16px',
            background: TT.inkRaised,
            border: `1px solid ${TT.inkBorder}`,
            borderLeft: `3px solid ${TT.yolk}`,
            borderRadius: '8px',
            padding: '14px 16px',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', flexWrap: 'wrap' }}>
            <div>
              <div style={{ color: TT.snow, fontSize: '15px', fontWeight: 600, marginBottom: '4px' }}>{selectedDocument.title}</div>
              <div style={{ color: TT.inkSubtle, fontSize: '12px' }}>
                {selectedDocument.sourceType} source • Created {formatDistanceToNow(new Date(selectedDocument.createdAt))} ago
              </div>
            </div>
            <StatusBadge status={selectedDocument.status} />
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '14px', marginTop: '12px', fontSize: '11px', color: TT.inkMuted }}>
            <span>{selectedDocument.chunkCount ?? 0} chunks</span>
            <span>{selectedDocument.tokenCount ?? 0} tokens</span>
            {selectedDocument.storageUrl ? (
              <a
                href={selectedDocument.storageUrl}
                target="_blank"
                rel="noreferrer"
                style={{ color: TT.yolk, textDecoration: 'none' }}
              >
                Open source
              </a>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* Empty state */}
      {documents.length === 0 ? (
        <div
          style={{
            padding: '48px 24px',
            textAlign: 'center',
            background: TT.inkRaised,
            borderRadius: '8px',
            border: `1px dashed ${TT.inkBorder}`,
            color: TT.inkSubtle,
          }}
        >
          <FileText size={32} style={{ margin: '0 auto 16px', display: 'block', opacity: 0.4 }} />
          <p style={{ marginBottom: '6px', color: TT.snow }}>No documents uploaded yet</p>
          <p style={{ fontSize: '12px' }}>Upload your first document to get started</p>
        </div>
      ) : filteredDocuments.length === 0 ? (
        <div
          style={{
            padding: '32px 24px',
            textAlign: 'center',
            background: TT.inkRaised,
            borderRadius: '8px',
            border: `1px dashed ${TT.inkBorder}`,
            color: TT.inkSubtle,
          }}
        >
          <p style={{ marginBottom: '6px', color: TT.snow }}>No documents match the current filters</p>
          <p style={{ fontSize: '12px', marginBottom: '14px' }}>Adjust the search, status, or sort controls to broaden the list.</p>
          <button
            type="button"
            onClick={() => {
              setSearchQuery('');
              setStatusFilter('all');
              setSortMode('recent');
            }}
            style={{
              background: TT.inkBlack,
              border: `1px solid ${TT.inkBorder}`,
              borderRadius: '6px',
              color: TT.yolk,
              padding: '8px 12px',
              cursor: 'pointer',
              fontSize: '12px',
            }}
          >
            Reset filters
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <AnimatePresence initial={false}>
            {filteredDocuments.map((doc) => (
              <DocumentRow
                key={doc.id}
                doc={doc}
                workspaceId={workspaceId}
                onDelete={handleDelete}
                onRetryOptimistic={handleRetryOptimistic}
                onReload={handleReload}
                canMutate={canMutateDocuments}
                selected={selectedDocument?.id === doc.id}
                onSelect={setSelectedDocumentId}
              />
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
