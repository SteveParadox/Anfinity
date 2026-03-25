/**
 * DocumentUploadView — production-ready document upload component
 *
 * Fixes vs. original:
 *  - Removed undefined `statusLoading` reference
 *  - Fixed `status?.status` (status is a string, not an object)
 *  - Replaced `alert()` with inline validation errors
 *  - Extracted constants, colour tokens, and sub-components
 *  - Added full ARIA / keyboard accessibility
 *  - Added upload-cancellation via AbortController
 *  - Added accepted-file-type guard with human-readable messages
 *  - Stabilised drop-zone class mutation via React state (no direct DOM manip)
 *  - Memoised pure helpers with useCallback / useMemo
 *  - Added onError / onStatusChange escape-hatch callbacks for parent app
 *  - Guard against missing workspace before upload
 */

import {
  useState,
  useRef,
  useContext,
  useCallback,
  useMemo,
  useEffect,
  KeyboardEvent,
} from 'react';
import {
  Upload,
  Check,
  AlertCircle,
  Loader2,
  FileText,
  X,
  RefreshCw,
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { useDocumentUpload } from '@/hooks/useDocumentUpload';
import { useDocumentIngestion } from '@/hooks/Ingestionhooks';
import { AuthContext } from '@/contexts/AuthContext';
import { isValidUUID } from '@/lib/uuidValidator';

// ─── Constants ────────────────────────────────────────────────────────────────

const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB

const ACCEPTED_MIME_TYPES: Record<string, string> = {
  'application/pdf': 'PDF',
  'text/plain': 'TXT',
  'text/markdown': 'MD',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
};

const ACCEPTED_EXTENSIONS = '.pdf,.txt,.md,.docx';

/** Maps ingestion status → rough progress percentage when the API has no `percentage`. */
const STATUS_PROGRESS: Record<string, number> = {
  idle: 0,
  downloading: 10,
  parsing: 25,
  chunking: 50,
  embedding: 75,
  completed: 100,
  failed: 0,
};

/** Statuses that represent an in-flight ingestion (disables "cancel" / nav away). */
const PROCESSING_STATUSES = new Set(['downloading', 'parsing', 'chunking', 'embedding']);

// ─── Design tokens ────────────────────────────────────────────────────────────

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
  successText: '#4CAF50',
  warnBg: 'rgba(245, 230, 66, 0.10)',
} as const;

// ─── Types ────────────────────────────────────────────────────────────────────

export interface DocumentUploadViewProps {
  /** Called with the new document ID once the file is uploaded to storage. */
  onUploadComplete?: (documentId: string) => void;
  /** Called whenever the ingestion status string changes. */
  onStatusChange?: (status: string) => void;
  /** Called with any unrecoverable error (upload or ingestion). */
  onError?: (error: string) => void;
  /** Called when workspace selection changes */
  onWorkspaceChange?: (workspaceId: string) => void;
}

// ─── Small presentational sub-components ──────────────────────────────────────

interface InlineErrorProps {
  message: string;
  onDismiss?: () => void;
  id?: string;
}

function InlineError({ message, onDismiss, id }: InlineErrorProps) {
  return (
    <div
      id={id}
      role="alert"
      aria-live="assertive"
      style={{
        background: TT.errorBg,
        border: `1px solid ${TT.errorBorder}`,
        borderRadius: '6px',
        padding: '12px 16px',
        marginBottom: '24px',
        color: TT.errorText,
        fontSize: '13px',
        display: 'flex',
        alignItems: 'center',
        gap: '12px',
      }}
    >
      <AlertCircle size={16} aria-hidden style={{ flexShrink: 0 }} />
      <span style={{ flex: 1 }}>{message}</span>
      {onDismiss && (
        <button
          onClick={onDismiss}
          aria-label="Dismiss error"
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: TT.errorText,
            opacity: 0.7,
            padding: 0,
            lineHeight: 1,
          }}
          onMouseEnter={(e) => (e.currentTarget.style.opacity = '1')}
          onMouseLeave={(e) => (e.currentTarget.style.opacity = '0.7')}
        >
          <X size={16} aria-hidden />
        </button>
      )}
    </div>
  );
}

interface StatCardProps {
  label: string;
  value: number | string;
}

function StatCard({ label, value }: StatCardProps) {
  return (
    <div style={{ background: TT.inkMid, padding: '12px', borderRadius: '6px' }}>
      <p style={{ fontSize: '11px', color: TT.inkSubtle, marginBottom: '4px' }}>{label}</p>
      <p style={{ fontSize: '18px', fontWeight: 600, color: TT.yolk }}>{value}</p>
    </div>
  );
}

// ─── Main component ────────────────────────────────────────────────────────────

export function DocumentUploadView({
  onUploadComplete,
  onStatusChange,
  onError,
}: DocumentUploadViewProps) {
  const authContext = useContext(AuthContext);
  const currentWorkspaceId = authContext?.currentWorkspaceId;
  const workspaces = authContext?.workspaces ?? [];

  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadedDocumentId, setUploadedDocumentId] = useState<string | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);

  const { uploadDocument, uploading, error: uploadError, clearError } = useDocumentUpload();
  const [selectedUploadWorkspaceId, setSelectedUploadWorkspaceId] = useState<string | null>(currentWorkspaceId);

  // Sync workspace selection with context changes
  useEffect(() => {
    setSelectedUploadWorkspaceId(currentWorkspaceId);
  }, [currentWorkspaceId]);

  const { status, progress, error: ingestionError } = useDocumentIngestion(
    uploadedDocumentId ?? '',
    selectedUploadWorkspaceId || currentWorkspaceId,
  );

  // ── Notify parent of status changes ──────────────────────────────────────
  useEffect(() => {
    if (status) onStatusChange?.(status);
  }, [status, onStatusChange]);

  // ── Propagate errors to parent ────────────────────────────────────────────
  useEffect(() => {
    if (uploadError) onError?.(uploadError);
  }, [uploadError, onError]);

  useEffect(() => {
    if (ingestionError) onError?.(ingestionError);
  }, [ingestionError, onError]);

  // ── Derived booleans ──────────────────────────────────────────────────────
  const isProcessing = PROCESSING_STATUSES.has(status ?? '');
  const isCompleted = status === 'completed';
  const isFailed = status === 'failed';

  // ── Progress percentage ───────────────────────────────────────────────────
  const progressPct = useMemo(() => {
    if (progress?.percentage !== undefined) return progress.percentage;
    return STATUS_PROGRESS[status ?? ''] ?? 0;
  }, [progress?.percentage, status]);

  // ── File validation ───────────────────────────────────────────────────────
  const validateFile = useCallback((file: File): string | null => {
    if (!ACCEPTED_MIME_TYPES[file.type]) {
      const supported = Object.values(ACCEPTED_MIME_TYPES).join(', ');
      return `"${file.name}" is not a supported file type. Please upload ${supported}.`;
    }
    if (file.size > MAX_FILE_SIZE_BYTES) {
      const sizeMB = (file.size / 1024 / 1024).toFixed(1);
      return `"${file.name}" is ${sizeMB} MB — exceeds the 50 MB limit.`;
    }
    return null;
  }, []);

  const handleFileSelect = useCallback(
    (file: File) => {
      const error = validateFile(file);
      if (error) {
        setValidationError(error);
        return;
      }
      setValidationError(null);
      clearError?.();
      setSelectedFile(file);
    },
    [validateFile, clearError],
  );

  // ── Drag-and-drop handlers ────────────────────────────────────────────────
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    // Only fire when truly leaving the zone (not entering a child)
    if (!e.currentTarget.contains(e.relatedTarget as Node)) {
      setIsDragOver(false);
    }
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFileSelect(file);
    },
    [handleFileSelect],
  );

  // ── Keyboard activation of the drop zone ─────────────────────────────────
  const handleDropZoneKeyDown = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      fileInputRef.current?.click();
    }
  }, []);

  // ── Upload ────────────────────────────────────────────────────────────────
  const handleUpload = useCallback(async () => {
    console.log('📤 [UPLOAD START] Starting document upload');
    
    if (!selectedFile) {
      console.warn('⚠️ [VALIDATION] No file selected');
      return;
    }

    if (!currentWorkspaceId) {
      console.error('❌ [WORKSPACE ERROR] No active workspace found');
      setValidationError('No active workspace found. Please select a workspace and try again.');
      return;
    }

    console.log('📋 [UPLOAD INFO] File: %s, Size: %d bytes, Workspace: %s', selectedFile.name, selectedFile.size, currentWorkspaceId);

    abortRef.current = new AbortController();
    console.debug('✅ [ABORT CONTROLLER] Created abort controller for upload cancellation');

    try {
      clearError?.();
      console.debug('📡 [API CALL] Calling uploadDocument()');
      const workspaceToUse = selectedUploadWorkspaceId || currentWorkspaceId;
      if (!workspaceToUse) {
        console.error('❌ [WORKSPACE VALIDATION] No workspace selected for upload');
        setValidationError('No workspace selected for upload. Please select a workspace.');
        return;
      }
      console.log('📦 [WORKSPACE CONTEXT] Uploading to workspace:', workspaceToUse);
      const doc = await uploadDocument(selectedFile, workspaceToUse, {
        signal: abortRef.current.signal,
      });
      console.log('✅ [UPLOAD SUCCESS] Document uploaded - ID:', doc.id);
      
      // ✅ VALIDATE: Ensure document ID is a valid UUID
      console.debug('🔍 [VALIDATE] Validating document ID format');
      if (!isValidUUID(doc.id)) {
        const errorMsg = `Invalid document ID received from server: "${doc.id}". This is a server-side error. Please try again or contact support.`;
        console.error('❌ [ID VALIDATION FAILED]', errorMsg);
        setValidationError(errorMsg);
        onError?.(errorMsg);
        return;
      }
      
      console.log('✅ [UPLOAD COMPLETE] Document validated - ID is valid UUID');
      setUploadedDocumentId(doc.id);
      setSelectedFile(null);
      onUploadComplete?.(doc.id);
    } catch (err) {
      if ((err as Error)?.name !== 'AbortError') {
        console.error('❌ [UPLOAD FAILED] Upload error:', err);
      } else {
        console.log('⏹️ [UPLOAD CANCELLED] Upload was cancelled by user');
      }
    } finally {
      abortRef.current = null;
      console.debug('🧹 [CLEANUP] Abort controller cleared');
    }
  }, [selectedFile, currentWorkspaceId, uploadDocument, clearError, onUploadComplete, onError]);

  const handleCancelUpload = useCallback(() => {
    console.log('⏹️ [CANCEL UPLOAD] User cancelled upload');
    console.debug('🛑 [ABORT] Aborting upload operation');
    abortRef.current?.abort();
    setSelectedFile(null);
    clearError?.();
    setValidationError(null);
  }, [clearError]);

  const handleRetry = useCallback(() => {
    console.log('🔄 [RETRY] User clicked retry - resetting upload state');
    setUploadedDocumentId(null);
    setSelectedFile(null);
    clearError?.();
    setValidationError(null);
  }, [clearError]);

  const handleUploadAnother = useCallback(() => {
    console.log('➕ [UPLOAD ANOTHER] User wants to upload another document');
    setUploadedDocumentId(null);
    setSelectedFile(null);
  }, []);

  // ── Drop-zone dynamic style ───────────────────────────────────────────────
  const dropZoneStyle: React.CSSProperties = {
    border: '2px dashed',
    borderColor: isDragOver || selectedFile ? TT.yolk : TT.inkBorder,
    borderRadius: '8px',
    padding: '48px',
    textAlign: 'center',
    cursor: 'pointer',
    background:
      isDragOver
        ? `rgba(245, 230, 66, 0.10)`
        : selectedFile
          ? `rgba(245, 230, 66, 0.05)`
          : TT.inkRaised,
    transition: 'all 0.2s ease',
    outline: 'none',
  };

  // ── Status badge colour ───────────────────────────────────────────────────
  const badgeStyle: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
    padding: '6px 12px',
    borderRadius: '4px',
    fontSize: '12px',
    fontWeight: 600,
    marginBottom: '24px',
    background: isCompleted ? TT.successBg : isFailed ? TT.errorBg : TT.warnBg,
    color: isCompleted ? TT.successText : isFailed ? TT.errorText : TT.yolk,
  };

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div
      style={{
        padding: '32px',
        minHeight: 'calc(100vh - 100px)',
        background: TT.inkBlack,
        color: TT.snow,
      }}
    >
      <div style={{ maxWidth: '900px', margin: '0 auto' }}>

        {/* ── Header ── */}
        <div style={{ marginBottom: '40px' }}>
          <h1 style={{ fontSize: '32px', fontWeight: 600, marginBottom: '8px' }}>
            Upload Documents
          </h1>
          <p style={{ color: TT.inkSubtle, fontSize: '14px' }}>
            Add PDFs, Word documents, or text files to your knowledge base
          </p>
        </div>

        {/* ── Workspace Selector ── */}
        {workspaces.length > 0 && (
          <div style={{ marginBottom: '32px', display: 'flex', alignItems: 'center', gap: '12px' }}>
            <label
              htmlFor="workspace-select"
              style={{ fontSize: '12px', fontWeight: 600, color: TT.inkSubtle, textTransform: 'uppercase', letterSpacing: '0.05em' }}
            >
              Destination Workspace:
            </label>
            <select
              id="workspace-select"
              value={selectedUploadWorkspaceId || ''}
              onChange={(e) => {
                const newWorkspaceId = e.target.value;
                setSelectedUploadWorkspaceId(newWorkspaceId);
                console.log('📦 [WORKSPACE SELECT] Changed to:', newWorkspaceId);
              }}
              style={{
                padding: '8px 12px',
                background: TT.inkRaised,
                border: `1px solid ${TT.inkBorder}`,
                borderRadius: '4px',
                color: TT.snow,
                fontSize: '13px',
                fontFamily: '"IBM Plex Mono", monospace',
                cursor: 'pointer',
                transition: 'all 0.2s ease',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLSelectElement).style.borderColor = TT.yolk;
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLSelectElement).style.borderColor = TT.inkBorder;
              }}
            >
              <option value="">Select workspace...</option>
              {workspaces.map((ws) => (
                <option key={ws.id} value={ws.id} style={{ background: TT.inkDeep, color: TT.snow }}>
                  {ws.name}
                </option>
              ))}
            </select>
          </div>
        )}

        <AnimatePresence mode="wait">

          {/* ── Upload panel ── */}
          {!uploadedDocumentId && (
            <motion.div
              key="upload-panel"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ duration: 0.25 }}
            >
              {/* Validation / upload errors */}
              {(validationError || uploadError) && (
                <InlineError
                  id="upload-error"
                  message={(validationError || uploadError) as string}
                  onDismiss={() => {
                    setValidationError(null);
                    clearError?.();
                  }}
                />
              )}

              {/* Drop zone */}
              <div
                role="button"
                tabIndex={0}
                aria-label="Select a file to upload. Drag and drop or press Enter to browse."
                aria-describedby={validationError || uploadError ? 'upload-error' : undefined}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                onKeyDown={handleDropZoneKeyDown}
                style={dropZoneStyle}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  hidden
                  aria-hidden
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) handleFileSelect(file);
                    // Reset so re-selecting the same file fires onChange
                    e.target.value = '';
                  }}
                  accept={ACCEPTED_EXTENSIONS}
                />

                {selectedFile ? (
                  <>
                    <FileText size={48} style={{ margin: '0 auto 16px', color: TT.yolk }} aria-hidden />
                    <h2 style={{ fontSize: '20px', fontWeight: 600, marginBottom: '8px' }}>
                      {selectedFile.name}
                    </h2>
                    <p style={{ color: TT.inkSubtle, fontSize: '13px' }}>
                      {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
                      {' · '}
                      {ACCEPTED_MIME_TYPES[selectedFile.type] ?? 'Unknown'}
                    </p>
                  </>
                ) : (
                  <>
                    <Upload size={48} style={{ margin: '0 auto 16px', color: TT.yolk }} aria-hidden />
                    <h2 style={{ fontSize: '20px', fontWeight: 600, marginBottom: '8px' }}>
                      Drag files here or click to select
                    </h2>
                    <p style={{ color: TT.inkSubtle, fontSize: '13px' }}>
                      Supported: PDF, DOCX, TXT, MD — max 50 MB
                    </p>
                  </>
                )}
              </div>

              {/* Actions */}
              {selectedFile && (
                <div style={{ marginTop: '24px', display: 'flex', gap: '12px' }}>
                  <button
                    onClick={handleUpload}
                    disabled={uploading}
                    aria-busy={uploading}
                    style={{
                      flex: 1,
                      padding: '12px 24px',
                      background: TT.yolk,
                      color: TT.inkBlack,
                      border: 'none',
                      borderRadius: '6px',
                      fontWeight: 600,
                      fontSize: '14px',
                      cursor: uploading ? 'not-allowed' : 'pointer',
                      opacity: uploading ? 0.7 : 1,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '8px',
                    }}
                  >
                    {uploading && (
                      <Loader2 size={16} aria-hidden className="spin" />
                    )}
                    {uploading ? 'Uploading…' : 'Upload Document'}
                  </button>
                  <button
                    onClick={handleCancelUpload}
                    disabled={uploading && !abortRef.current}
                    style={{
                      padding: '12px 24px',
                      background: TT.inkRaised,
                      color: TT.snow,
                      border: `1px solid ${TT.inkBorder}`,
                      borderRadius: '6px',
                      fontWeight: 600,
                      fontSize: '14px',
                      cursor: 'pointer',
                    }}
                  >
                    {uploading ? 'Cancel Upload' : 'Clear'}
                  </button>
                </div>
              )}
            </motion.div>
          )}

          {/* ── Processing panel ── */}
          {uploadedDocumentId && (
            <motion.div
              key="processing-panel"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ duration: 0.25 }}
              style={{
                background: TT.inkRaised,
                border: `1px solid ${TT.inkBorder}`,
                borderRadius: '8px',
                padding: '32px',
              }}
            >
              {/* Title row */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
                <FileText size={24} style={{ color: TT.yolk }} aria-hidden />
                <div>
                  <h3 style={{ fontSize: '16px', fontWeight: 600 }}>Processing Document</h3>
                  <p style={{ fontSize: '12px', color: TT.inkSubtle }}>
                    ID: <code style={{ fontFamily: 'monospace' }}>{uploadedDocumentId.slice(0, 8)}…</code>
                  </p>
                </div>
              </div>

              {/* Status badge */}
              {status && status !== 'idle' && (
                <div style={badgeStyle} role="status" aria-live="polite">
                  {isCompleted && <><Check size={14} aria-hidden /> Complete</>}
                  {isFailed && <><AlertCircle size={14} aria-hidden /> Failed</>}
                  {isProcessing && (
                    <>
                      <Loader2 size={14} aria-hidden className="spin" />
                      {status.charAt(0).toUpperCase() + status.slice(1)}…
                    </>
                  )}
                </div>
              )}

              {/* Ingestion error */}
              {isFailed && ingestionError && (
                <InlineError
                  message={`Document processing failed: ${ingestionError}`}
                />
              )}

              {/* Progress bar */}
              {status && status !== 'idle' && (
                <div style={{ marginBottom: '24px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
                    <span style={{ fontSize: '13px' }}>Processing progress</span>
                    <span style={{ fontSize: '13px', color: TT.inkSubtle }}>
                      {progressPct}%
                    </span>
                  </div>
                  <div
                    role="progressbar"
                    aria-valuenow={progressPct}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label="Document ingestion progress"
                    style={{
                      height: '6px',
                      background: TT.inkMid,
                      borderRadius: '3px',
                      overflow: 'hidden',
                    }}
                  >
                    <div
                      style={{
                        height: '100%',
                        background: isFailed ? TT.errorText : TT.yolk,
                        width: `${progressPct}%`,
                        transition: 'width 0.4s ease',
                      }}
                    />
                  </div>
                </div>
              )}

              {/* Stats */}
              {progress?.details && (
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(3, 1fr)',
                    gap: '12px',
                    marginBottom: '24px',
                  }}
                >
                  <StatCard label="Chunks" value={progress.details.chunks_created ?? 0} />
                  <StatCard label="Embeddings" value={progress.details.embeddings_created ?? 0} />
                  <StatCard label="Tokens" value={progress.details.total_tokens ?? 0} />
                </div>
              )}

              {/* Actions */}
              {isFailed ? (
                <div style={{ display: 'flex', gap: '12px' }}>
                  <button
                    onClick={handleRetry}
                    style={{
                      flex: 1,
                      padding: '12px 24px',
                      background: TT.yolk,
                      color: TT.inkBlack,
                      border: 'none',
                      borderRadius: '6px',
                      fontWeight: 600,
                      fontSize: '14px',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '8px',
                    }}
                  >
                    <RefreshCw size={14} aria-hidden />
                    Try Again
                  </button>
                  <button
                    onClick={handleUploadAnother}
                    style={{
                      flex: 1,
                      padding: '12px 24px',
                      background: TT.inkMid,
                      color: TT.snow,
                      border: `1px solid ${TT.inkBorder}`,
                      borderRadius: '6px',
                      fontWeight: 600,
                      fontSize: '14px',
                      cursor: 'pointer',
                    }}
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  onClick={handleUploadAnother}
                  disabled={isProcessing}
                  aria-disabled={isProcessing}
                  style={{
                    width: '100%',
                    padding: '12px 24px',
                    background: TT.inkMid,
                    color: TT.snow,
                    border: `1px solid ${TT.inkBorder}`,
                    borderRadius: '6px',
                    fontWeight: 600,
                    fontSize: '14px',
                    cursor: isProcessing ? 'not-allowed' : 'pointer',
                    opacity: isProcessing ? 0.5 : 1,
                  }}
                >
                  {isCompleted ? 'Upload Another Document' : 'Cancel'}
                </button>
              )}
            </motion.div>
          )}

        </AnimatePresence>
      </div>

      {/* ── Global styles ── */}
      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
        .spin { animation: spin 1s linear infinite; }
      `}</style>
    </div>
  );
}