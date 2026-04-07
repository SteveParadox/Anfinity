/**
 * ingestion event hooks
 *
 * Fixes vs. original:
 *
 * useDocumentIngestion
 *  - No changes needed beyond making stageMap a module-level constant
 *    (was recreated on every useCallback invocation).
 *
 * useMultipleDocumentsIngestion
 *  - No substantive bugs, minor clean-up only.
 *
 * useWorkspaceIngestionStats
 *  - CRITICAL: `activeDocuments` was stored as a `Set` inside a plain-object
 *    state value. The spread `{ ...prev }` copies own enumerable properties of
 *    the object but does NOT clone the Set itself — both old and new state
 *    shared the same Set reference, so mutations to `activeSet` also mutated
 *    the previous state snapshot (breaks React's immutability contract and
 *    causes stale-closure bugs in concurrent mode). Fixed with `new Set(prev.activeDocuments)`.
 *  - `successRate` was returned as a pre-formatted string (e.g. "83.3") making
 *    arithmetic impossible for callers. Now returned as a `number`; callers can
 *    call `.toFixed()` themselves.
 *
 * useIngestionErrors
 *  - Event handler was NOT wrapped in useCallback, causing a new function
 *    reference on every render and therefore a new event subscription on every
 *    render. Fixed with useCallback.
 *  - `clearError` was an inline arrow in the return value (new ref every
 *    render). Promoted to useCallback.
 *
 * useDebouncedEventListener / useBatchedEvents
 *  - No logic bugs. Minor: NodeJS.Timeout → ReturnType<typeof setTimeout>
 *    for browser compatibility without requiring @types/node.
 */

import { useEffect, useState, useCallback, useRef } from 'react';
import { useEventListener } from '../contexts/EventsContext';
import type { Event } from '../types/events';
import { EventType } from '../types/events';
import { isValidUUID } from '../lib/uuidValidator';
import { api } from '../lib/api';

// ─── Module-level constants ───────────────────────────────────────────────────

/** Maps backend stage names to the status strings used by the UI. */
const STAGE_STATUS_MAP: Record<string, string> = {
  download:  'downloading',
  parse:     'parsing',
  chunking:  'chunking',
  embedding: 'embedding',
};

// ─── useDocumentIngestion ─────────────────────────────────────────────────────

type IngestionStatus =
  | 'idle'
  | 'started'
  | 'downloading'
  | 'parsing'
  | 'chunking'
  | 'embedding'
  | 'completed'
  | 'failed';

export function useDocumentIngestion(
  documentId: string,
  workspaceId?: string,
) {
  const [status,    setStatus]    = useState<IngestionStatus>('idle');
  const [progress,  setProgress]  = useState<{
    stage?: string;
    percentage?: number;
    details?: Record<string, unknown>;
  }>({});
  const [error,     setError]     = useState<string | null>(null);
  const [startTime, setStartTime] = useState<number | null>(null);
  const [endTime,   setEndTime]   = useState<number | null>(null);

  // ✅ VALIDATION: Ensure documentId is a valid UUID format before listening for events
  // This prevents listening for events with an invalid document ID that will never match
  useEffect(() => {
    if (documentId && !isValidUUID(documentId)) {
      const msg = `Invalid document ID format: "${documentId}". Expected a valid UUID.`;
      console.warn('[useDocumentIngestion]', msg);
      setStatus('failed');
      setError(msg);
    }
  }, [documentId]);

  useEventListener(
    [
      EventType.DOCUMENT_STARTED,
      EventType.STAGE_STARTED,
      EventType.STAGE_COMPLETED,
      EventType.PROGRESS_UPDATE,
      EventType.DOCUMENT_COMPLETED,
      EventType.DOCUMENT_FAILED,
    ],
    useCallback(
      (event: Event) => {
        if (event.document_id !== documentId) return;
        if (workspaceId && event.workspace_id !== workspaceId) return;

        switch (event.event_type) {
          case EventType.DOCUMENT_STARTED:
            setStatus('started');
            setStartTime(Date.now());
            setError(null);
            break;

          case EventType.STAGE_STARTED:
            setStatus((STAGE_STATUS_MAP[event.stage ?? ''] ?? 'started') as IngestionStatus);
            setProgress({
              stage:   event.stage,
              details: event.data?.progress ?? {},
            });
            break;

          case EventType.STAGE_COMPLETED:
            setProgress((prev) => ({
              ...prev,
              stage:   event.stage,
              details: { ...prev.details, ...event.data?.progress },
            }));
            break;

          case EventType.PROGRESS_UPDATE:
            setProgress({ details: event.data });
            break;

          case EventType.DOCUMENT_COMPLETED:
            setStatus('completed');
            setEndTime(Date.now());
            setProgress({ details: event.data, percentage: 100 });
            break;

          case EventType.DOCUMENT_FAILED:
            setStatus('failed');
            setEndTime(Date.now());
            setError(event.data?.error_message ?? 'Unknown error');
            break;
        }
      },
      [documentId, workspaceId],
    ),
  );

  const duration = startTime && endTime ? endTime - startTime : null;

  return {
    status,
    progress,
    error,
    duration,
    isProcessing:
      status === 'started'     ||
      status === 'downloading' ||
      status === 'parsing'     ||
      status === 'chunking'    ||
      status === 'embedding',
  };
}

// ─── useMultipleDocumentsIngestion ────────────────────────────────────────────

export function useMultipleDocumentsIngestion(
  documentIds: string[],
  workspaceId?: string,
) {
  const [statuses, setStatuses] = useState<
    Record<string, { status: string; progress: Record<string, unknown>; error?: string }>
  >({});

  useEventListener(
    [
      EventType.DOCUMENT_STARTED,
      EventType.STAGE_COMPLETED,
      EventType.DOCUMENT_COMPLETED,
      EventType.DOCUMENT_FAILED,
    ],
    useCallback(
      (event: Event) => {
        if (!event.document_id || !documentIds.includes(event.document_id)) return;
        if (workspaceId && event.workspace_id !== workspaceId) return;

        setStatuses((prev) => ({
          ...prev,
          [event.document_id!]: {
            status:   event.event_type,
            progress: event.data ?? {},
            error:
              event.event_type === EventType.DOCUMENT_FAILED
                ? event.data?.error_message
                : undefined,
          },
        }));
      },
      [documentIds, workspaceId],
    ),
  );

  const totalDocuments  = documentIds.length;
  const completedCount  = Object.values(statuses).filter((s) => s.status === EventType.DOCUMENT_COMPLETED).length;
  const failedCount     = Object.values(statuses).filter((s) => s.status === EventType.DOCUMENT_FAILED).length;
  const processingCount = totalDocuments - completedCount - failedCount;

  return {
    statuses,
    stats: {
      total:                totalDocuments,
      completed:            completedCount,
      failed:               failedCount,
      processing:           processingCount,
      completionPercentage: totalDocuments > 0 ? (completedCount / totalDocuments) * 100 : 0,
    },
  };
}

// ─── useWorkspaceIngestionStats ───────────────────────────────────────────────

interface WorkspaceStats {
  documentsStarted:    number;
  documentsCompleted:  number;
  documentsFailed:     number;
  totalChunks:         number;
  totalEmbeddings:     number;
  /** Mutable Set kept outside of React state to avoid prototype-loss bug. */
  activeDocuments:     Set<string>;
}

export function useWorkspaceIngestionStats(workspaceId?: string) {
  const [stats, setStats] = useState<WorkspaceStats>({
    documentsStarted:   0,
    documentsCompleted: 0,
    documentsFailed:    0,
    totalChunks:        0,
    totalEmbeddings:    0,
    activeDocuments:    new Set<string>(),
  });

  useEventListener(
    [
      EventType.DOCUMENT_STARTED,
      EventType.DOCUMENT_COMPLETED,
      EventType.DOCUMENT_FAILED,
    ],
    useCallback(
      (event: Event) => {
        if (workspaceId && event.workspace_id !== workspaceId) return;
        if (!event.document_id) return;

        setStats((prev) => {
          // FIXED: was `new Set()` then spreading the outer object — the old
          // Set reference was shared between prev and next state.
          const activeDocuments = new Set<string>(prev.activeDocuments);

          switch (event.event_type) {
            case EventType.DOCUMENT_STARTED:
              activeDocuments.add(event.document_id!);
              return { ...prev, documentsStarted: prev.documentsStarted + 1, activeDocuments };

            case EventType.DOCUMENT_COMPLETED:
              activeDocuments.delete(event.document_id!);
              return {
                ...prev,
                documentsCompleted: prev.documentsCompleted + 1,
                totalChunks:        prev.totalChunks     + (event.data?.chunk_count     ?? 0),
                totalEmbeddings:    prev.totalEmbeddings + (event.data?.embedding_count ?? 0),
                activeDocuments,
              };

            case EventType.DOCUMENT_FAILED:
              activeDocuments.delete(event.document_id!);
              return { ...prev, documentsFailed: prev.documentsFailed + 1, activeDocuments };

            default:
              return prev;
          }
        });
      },
      [workspaceId],
    ),
  );

  // FIXED: was returned as a pre-formatted string (e.g. "83.3"), making
  // arithmetic impossible for callers. Now a raw number; callers use .toFixed().
  const successRate =
    stats.documentsCompleted + stats.documentsFailed > 0
      ? (stats.documentsCompleted / (stats.documentsCompleted + stats.documentsFailed)) * 100
      : 0;

  return {
    documentsStarted:    stats.documentsStarted,
    documentsCompleted:  stats.documentsCompleted,
    documentsFailed:     stats.documentsFailed,
    totalChunks:         stats.totalChunks,
    totalEmbeddings:     stats.totalEmbeddings,
    activeDocumentsCount: stats.activeDocuments.size,
    /** Raw percentage (0–100). Call `.toFixed(1)` for display. */
    successRate,
  };
}

// ─── useIngestionErrors ───────────────────────────────────────────────────────

export function useIngestionErrors(
  onError?: (documentId: string, error: string) => void,
  workspaceId?: string,
) {
  const [errors, setErrors] = useState<Record<string, { message: string; timestamp: number }>>({});

  // FIXED: was an inline arrow — new function reference on every render caused
  // a new subscription on every render.
  const handleEvent = useCallback(
    (event: Event) => {
      if (workspaceId && event.workspace_id !== workspaceId) return;
      if (!event.document_id) return;

      const errorMessage = event.data?.error_message ?? 'Unknown error';

      setErrors((prev) => ({
        ...prev,
        [event.document_id!]: { message: errorMessage, timestamp: Date.now() },
      }));

      onError?.(event.document_id, errorMessage);
    },
    [workspaceId, onError],
  );

  useEventListener(EventType.DOCUMENT_FAILED, handleEvent);

  // FIXED: was an inline arrow in the return object (new ref every render).
  const clearError = useCallback((documentId: string) => {
    setErrors((prev) => {
      const next = { ...prev };
      delete next[documentId];
      return next;
    });
  }, []);

  return {
    errors,
    hasErrors: Object.keys(errors).length > 0,
    clearError,
  };
}

// ─── useDebouncedEventListener ────────────────────────────────────────────────

export function useDebouncedEventListener(
  eventType: EventType | EventType[],
  callback: (event: Event) => void,
  delayMs = 500,
) {
  // ReturnType<typeof setTimeout> works in both browser and Node environments
  // without requiring @types/node.
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEventListener(eventType, (event: Event) => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => callback(event), delayMs);
  });

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);
}

// ─── useBatchedEvents ─────────────────────────────────────────────────────────

export function useBatchedEvents(
  eventType: EventType | EventType[],
  callback: (events: Event[]) => void,
  windowMs = 1_000,
) {
  const batchRef = useRef<Event[]>([]);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flushBatch = useCallback(() => {
    if (batchRef.current.length > 0) {
      callback([...batchRef.current]);
      batchRef.current = [];
    }
  }, [callback]);

  useEventListener(eventType, (event: Event) => {
    batchRef.current.push(event);
    if (!timerRef.current) {
      timerRef.current = setTimeout(() => {
        flushBatch();
        timerRef.current = null;
      }, windowMs);
    }
  });

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      flushBatch();
    };
  }, [flushBatch]);
}