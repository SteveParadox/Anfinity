/**
 * Example components showing how to use the real-time event system.
 *
 * These are production-ready examples that can be adapted to your needs.
 */

import React from 'react';
import {
  useDocumentIngestion,
  useWorkspaceIngestionStats,
  useIngestionErrors,
  useMultipleDocumentsIngestion,
} from '../hooks/useIngestion';
import { useEventConnection } from '../contexts/EventsContext';

/**
 * Component to display ingestion progress for a single document.
 *
 * Shows:
 * - Current stage (downloading, parsing, chunking, embedding)
 * - Progress details
 * - Estimated time remaining
 * - Errors if any
 */
export function DocumentIngestionCard({
  documentId,
  documentTitle,
  workspaceId,
}: {
  documentId: string;
  documentTitle: string;
  workspaceId: string;
}) {
  const { status, progress, error, duration, isProcessing } =
    useDocumentIngestion(documentId, workspaceId);

  const stageEmojis: Record<string, string> = {
    started: '📄',
    downloading: '⬇️',
    parsing: '📖',
    chunking: '✂️',
    embedding: '🧠',
    completed: '✅',
    failed: '❌',
  };

  // Estimate time based on stage
  const estimatedTotalTime: Record<string, number> = {
    downloading: 5000,
    parsing: 10000,
    chunking: 15000,
    embedding: 30000,
  };

  const stageTime =
    estimatedTotalTime[status as keyof typeof estimatedTotalTime] || 0;
  const estimatedRemaining = Math.max(0, stageTime - (duration || 0));

  return (
    <div
      style={{
        border: '1px solid #e5e7eb',
        borderRadius: '8px',
        padding: '16px',
        marginBottom: '12px',
        background: error ? '#fee2e2' : isProcessing ? '#fef3c7' : '#f0fdf4',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span style={{ fontSize: '20px' }}>
          {stageEmojis[status] || '⏳'}
        </span>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600 }}>{documentTitle}</div>
          <div style={{ fontSize: '12px', color: '#6b7280' }}>
            {status === 'started'
              ? 'Starting...'
              : status === 'downloading'
                ? 'Downloading...'
                : status === 'parsing'
                  ? 'Parsing document...'
                  : status === 'chunking'
                    ? `Creating chunks...`
                    : status === 'embedding'
                      ? 'Generating embeddings...'
                      : status === 'completed'
                        ? `Completed in ${(duration || 0) / 1000}s`
                        : error || status}
          </div>
        </div>
      </div>

      {/* Progress bar */}
      {isProcessing && (
        <div
          style={{
            width: '100%',
            height: '8px',
            backgroundColor: '#e5e7eb',
            borderRadius: '4px',
            marginTop: '12px',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              width: `${
                status === 'downloading'
                  ? 25
                  : status === 'parsing'
                    ? 50
                    : status === 'chunking'
                      ? 75
                      : 90
              }%`,
              height: '100%',
              backgroundColor: '#3b82f6',
              transition: 'width 0.3s ease',
            }}
          />
        </div>
      )}

      {/* Details */}
      {progress.details && Object.keys(progress.details).length > 0 && (
        <div style={{ marginTop: '12px', fontSize: '12px', color: '#6b7280' }}>
          {progress.details.bytes_downloaded && (
            <div>
              Downloaded: {(progress.details.bytes_downloaded / 1024).toFixed(1)} KB
            </div>
          )}
          {progress.details.chunks_created && (
            <div>Chunks: {progress.details.chunks_created}</div>
          )}
          {progress.details.vectors_created && (
            <div>Embeddings: {progress.details.vectors_created}</div>
          )}
          {estimatedRemaining > 0 && (
            <div>
              Est. time: {(estimatedRemaining / 1000).toFixed(0)}s
            </div>
          )}
        </div>
      )}

      {/* Error */}
      {error && (
        <div
          style={{
            marginTop: '12px',
            padding: '8px',
            backgroundColor: '#fecaca',
            borderRadius: '4px',
            fontSize: '12px',
            color: '#991b1b',
          }}
        >
          Error: {error}
        </div>
      )}
    </div>
  );
}

/**
 * Widget showing workspace-level ingestion statistics.
 *
 * Displays:
 * - Total documents processed
 * - Active documents being processed
 * - Success rate
 * - Total chunks and embeddings created
 */
export function WorkspaceIngestionStatsWidget({
  workspaceId,
}: {
  workspaceId: string;
}) {
  const stats = useWorkspaceIngestionStats(workspaceId);
  const { connected, error } = useEventConnection();

  return (
    <div
      style={{
        padding: '16px',
        border: '1px solid #e5e7eb',
        borderRadius: '8px',
        background: '#f9fafb',
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: '12px' }}>
        📊 Ingestion Stats
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
        <StatBox
          label="Completed"
          value={stats.documentsCompleted}
          color="#10b981"
        />
        <StatBox
          label="Failed"
          value={stats.documentsFailed}
          color="#ef4444"
        />
        <StatBox
          label="Processing"
          value={stats.activeDocumentsCount}
          color="#f59e0b"
        />
        <StatBox
          label="Success Rate"
          value={`${stats.successRate}%`}
          color="#3b82f6"
        />
      </div>

      <div style={{ marginTop: '12px', fontSize: '12px', color: '#6b7280' }}>
        <div>Total chunks: {stats.totalChunks}</div>
        <div>Total embeddings: {stats.totalEmbeddings}</div>
      </div>

      {/* Connection status */}
      <div
        style={{
          marginTop: '12px',
          padding: '8px',
          borderRadius: '4px',
          background: connected ? '#dcfce7' : '#fee2e2',
          fontSize: '12px',
          color: connected ? '#166534' : '#991b1b',
        }}
      >
        {connected ? '✅ Connected' : `❌ Disconnected${error ? ': ' + error.message : ''}`}
      </div>
    </div>
  );
}

/**
 * Component to display multiple documents' ingestion progress.
 *
 * Shows a progress overview for bulk operations.
 */
export function BulkDocumentIngestionProgress({
  documentIds,
  workspaceId,
}: {
  documentIds: string[];
  workspaceId: string;
}) {
  const { stats } = useMultipleDocumentsIngestion(documentIds, workspaceId);

  const progressSegments = [
    { label: 'Processing', count: stats.processing, color: '#f59e0b' },
    { label: 'Completed', count: stats.completed, color: '#10b981' },
    { label: 'Failed', count: stats.failed, color: '#ef4444' },
  ];

  return (
    <div style={{ padding: '16px', border: '1px solid #e5e7eb', borderRadius: '8px' }}>
      <div style={{ fontWeight: 600, marginBottom: '12px' }}>
        Documents: {stats.completed + stats.failed}/{stats.total}
      </div>

      {/* Progress bar with segments */}
      <div
        style={{
          display: 'flex',
          height: '24px',
          borderRadius: '4px',
          overflow: 'hidden',
          gap: '1px',
          background: '#e5e7eb',
        }}
      >
        {progressSegments.map((segment) => (
          <div
            key={segment.label}
            style={{
              width: `${(segment.count / stats.total) * 100}%`,
              background: segment.color,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'white',
              fontSize: '11px',
              fontWeight: 600,
            }}
            title={`${segment.label}: ${segment.count}`}
          >
            {segment.count > 0 && `${segment.count}`}
          </div>
        ))}
      </div>

      {/* Legend */}
      <div
        style={{
          marginTop: '12px',
          display: 'flex',
          gap: '16px',
          fontSize: '12px',
        }}
      >
        {progressSegments.map((segment) => (
          <div key={segment.label} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <div
              style={{
                width: '12px',
                height: '12px',
                background: segment.color,
                borderRadius: '2px',
              }}
            />
            {segment.label}: {segment.count}
          </div>
        ))}
      </div>

      {/* Completion percentage */}
      <div
        style={{
          marginTop: '12px',
          fontSize: '14px',
          fontWeight: 600,
          color: '#374151',
        }}
      >
        {stats.completionPercentage.toFixed(0)}% Complete
      </div>
    </div>
  );
}

/**
 * Error notification list component.
 */
export function IngestionErrorNotifications({
  workspaceId,
}: {
  workspaceId: string;
}) {
  const { errors, clearError } = useIngestionErrors(undefined, workspaceId);

  if (Object.keys(errors).length === 0) {
    return null;
  }

  return (
    <div style={{ position: 'fixed', top: '20px', right: '20px', zIndex: 1000 }}>
      {Object.entries(errors).map(([docId, error]) => (
        <div
          key={docId}
          style={{
            background: '#fee2e2',
            border: '1px solid #fecaca',
            borderRadius: '8px',
            padding: '12px',
            marginBottom: '8px',
            color: '#991b1b',
            maxWidth: '400px',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <div>
            <div style={{ fontWeight: 600, marginBottom: '4px' }}>Error</div>
            <div style={{ fontSize: '12px' }}>{error.message}</div>
          </div>
          <button
            onClick={() => clearError(docId)}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              fontSize: '18px',
              marginLeft: '12px',
            }}
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}

/**
 * Helper component for stat boxes.
 */
function StatBox({
  label,
  value,
  color,
}: {
  label: string;
  value: string | number;
  color: string;
}) {
  return (
    <div
      style={{
        padding: '12px',
        border: `2px solid ${color}`,
        borderRadius: '6px',
        background: '#f5f5f5',
      }}
    >
      <div style={{ fontSize: '12px', color: '#6b7280', marginBottom: '4px' }}>
        {label}
      </div>
      <div style={{ fontSize: '20px', fontWeight: 600, color }}>
        {value}
      </div>
    </div>
  );
}
