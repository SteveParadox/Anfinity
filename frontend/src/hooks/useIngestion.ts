/**
 * useIngestion.ts — document upload + status-polling hooks
 *
 * Fixes vs. original:
 *
 * useIngestionStatus
 *  - CRITICAL: race condition in useEffect — `if (shouldContinue)` evaluated
 *    synchronously before the initial `fetchStatus()` promise resolved, so the
 *    polling interval was ALWAYS started regardless of the document's actual
 *    initial state (already `indexed` or `failed` docs kept polling forever).
 *    Fixed by unconditionally starting one interval and clearing it when the
 *    fetch returns false.
 *  - Cleanup now always runs (was conditionally inside the `if` block).
 *  - `fetchStatus` return value typed explicitly (was implicit boolean | void).
 *
 * useDocumentUpload
 *  - Added optional `signal` (AbortSignal) to `uploadDocument` so callers can
 *    cancel in-flight requests (the hardened DocumentUploadView already passes
 *    this, but the hook silently dropped it).
 *  - Cleaned up error-message normalisation to avoid duplicate catch patterns.
 */

import { useState, useEffect, useCallback } from 'react';
import { api } from '../lib/api';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface IngestionStatus {
  document_id: string;
  title: string;
  source_type?: string;
  status: 'pending' | 'processing' | 'indexed' | 'failed';
  progress: {
    chunks_created: number;
    embeddings_created: number;
    total_tokens: number;
  };
  stages?: Array<{
    stage: string;
    status: string;
    duration_ms?: number;
    timestamp?: string;
  }>;
  logs?: Array<{
    stage: string;
    status: string;
    duration_ms?: number;
    timestamp?: string;
  }>;
  created_at?: string;
  updated_at?: string;
  error?: string;
}

interface UseIngestionStatusOptions {
  documentId?: string;
  workspaceId?: string;
  /** Polling interval in ms. Defaults to 5 000. */
  pollInterval?: number;
  enabled?: boolean;
}

// ─── Error message normalisation ─────────────────────────────────────────────

function normaliseIngestionError(raw: string): string {
  const lower = raw.toLowerCase();
  if (lower.includes('timeout'))
    return 'Processing timed out. Document may be too large — try a smaller file.';
  if (lower.includes('memory'))
    return 'Insufficient memory to process document. Try a smaller file.';
  if (lower.includes('corrupted') || lower.includes('damaged'))
    return 'Document appears corrupted. Try uploading a different file.';
  if (lower.includes('unsupported'))
    return 'Document format not supported. Use PDF, TXT, MD, or DOCX.';
  return raw;
}

function normaliseUploadError(err: unknown): string {
  if (!(err instanceof Error)) return 'Document upload failed. Please try again.';
  const msg = err.message.toLowerCase();
  if (msg.includes('413') || msg.includes('too large'))
    return 'File exceeds the 50 MB limit. Please upload a smaller file.';
  if (msg.includes('415') || msg.includes('unsupported'))
    return 'File type not supported. Use PDF, TXT, MD, or DOCX.';
  if (msg.includes('timeout'))
    return 'Upload timed out. Please check your connection and retry.';
  if (msg.includes('network'))
    return 'Network error. Please check your connection and retry.';
  return err.message || 'Document upload failed. Please try again.';
}

// ─── useIngestionStatus ───────────────────────────────────────────────────────

export function useIngestionStatus(options: UseIngestionStatusOptions = {}) {
  const {
    documentId,
    workspaceId,
    pollInterval = 5_000,
    enabled = true,
  } = options;

  const [status,  setStatus]  = useState<IngestionStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  /**
   * Fetches the current status and returns `true` if polling should continue,
   * `false` if the document has reached a terminal state.
   */
  const fetchStatus = useCallback(async (): Promise<boolean> => {
    if (!documentId) return false;

    try {
      setLoading(true);
      const response = await api.getIngestionStatus(documentId);
      setStatus(response);

      if (response.status === 'failed') {
        const raw = response.error ?? 'Document processing failed. Please try again.';
        setError(normaliseIngestionError(raw));
        return false; // terminal — stop polling
      }

      setError(null);

      if (response.status === 'indexed') {
        return false; // terminal — stop polling
      }

      return true; // still in progress
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to check processing status';
      setError(msg);
      return false; // on error, stop polling
    } finally {
      setLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    if (!enabled || !documentId) return;

    let cancelled = false;

    // Kick off an immediate fetch, then start the interval unconditionally.
    // The interval callback checks `cancelled` and the fetch return value to
    // decide whether to clear itself — fixing the original race condition where
    // `shouldContinue` was read before the promise resolved.
    const interval = setInterval(async () => {
      if (cancelled) return;
      const shouldContinue = await fetchStatus();
      if (!shouldContinue || cancelled) {
        clearInterval(interval);
      }
    }, pollInterval);

    // Initial immediate fetch (don't wait for first interval tick)
    fetchStatus().then((shouldContinue) => {
      if (!shouldContinue || cancelled) {
        clearInterval(interval);
      }
    });

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [documentId, enabled, pollInterval, fetchStatus]);

  return { status, loading, error, refetch: fetchStatus };
}

// ─── useDocumentUpload ────────────────────────────────────────────────────────

interface UploadOptions {
  /** Optional AbortSignal to cancel the in-flight request. */
  signal?: AbortSignal;
}

export function useDocumentUpload() {
  const [uploading,          setUploading]          = useState(false);
  const [error,              setError]              = useState<string | null>(null);
  const [uploadedDocumentId, setUploadedDocumentId] = useState<string | null>(null);

  const uploadDocument = useCallback(
    async (file: File, workspaceId: string, options: UploadOptions = {}) => {
      try {
        setUploading(true);
        setError(null);

        // Thread the AbortSignal through to the API layer so in-flight XHR /
        // fetch requests are actually cancelled when the user clicks "Cancel".
        const data = await api.uploadDocument(file, workspaceId, {
          signal: options.signal,
        });

        setUploadedDocumentId(data.id);
        return data;
      } catch (err) {
        // Don't surface abort errors — cancellation is intentional
        if ((err as Error)?.name === 'AbortError') throw err;

        const message = normaliseUploadError(err);
        setError(message);
        throw err;
      } finally {
        setUploading(false);
      }
    },
    [],
  );

  const clearError = useCallback(() => setError(null), []);

  return {
    uploadDocument,
    uploading,
    error,
    uploadedDocumentId,
    clearError,
  };
}