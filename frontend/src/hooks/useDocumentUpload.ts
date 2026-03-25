/**
 * Hook for document upload and ingestion status polling
 */

import { useState, useEffect, useCallback } from 'react';
import { api } from '../lib/api';

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
  pollInterval?: number; // ms
  enabled?: boolean;
}

export function useIngestionStatus(options: UseIngestionStatusOptions = {}) {
  const { 
    documentId, 
    workspaceId, 
    pollInterval = 5000,  // Increased from 2s to 5s for better backend efficiency
    enabled = true 
  } = options;

  const [status, setStatus] = useState<IngestionStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    if (!documentId) return;

    try {
      setLoading(true);
      const response = await api.getIngestionStatus(documentId);
      setStatus(response);
      
      // If ingestion failed, extract specific error message
      if (response.status === 'failed') {
        let errorMsg = response.error || 'Document processing failed. Please try again.';
        
        // Parse common error patterns for better UX
        const errorLower = errorMsg.toLowerCase();
        if (errorLower.includes('timeout')) {
          errorMsg = 'Processing timed out. Document may be too large. Try a smaller file.';
        } else if (errorLower.includes('memory')) {
          errorMsg = 'Insufficient memory to process document. Try a smaller file.';
        } else if (errorLower.includes('corrupted') || errorLower.includes('damaged')) {
          errorMsg = 'Document appears corrupted. Try uploading a different file.';
        } else if (errorLower.includes('unsupported')) {
          errorMsg = 'Document format not supported. Use PDF, TXT, MD, or DOCX.';
        }
        
        setError(errorMsg);
      } else {
        setError(null);
      }

      // Stop polling once indexed or failed
      if (response.status === 'indexed' || response.status === 'failed') {
        return false;
      }
      return true;
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : 'Failed to check processing status';
      setError(errMsg);
      return false;
    } finally {
      setLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    if (!enabled || !documentId) return;

    // Fetch immediately
    let shouldContinue = true;
    fetchStatus().then((canContinue) => {
      shouldContinue = canContinue !== false;
    });

    // Set up interval
    if (shouldContinue) {
      const interval = setInterval(() => {
        fetchStatus().then((canContinue) => {
          if (canContinue === false) {
            clearInterval(interval);
          }
        });
      }, pollInterval);

      return () => clearInterval(interval);
    }
  }, [documentId, enabled, pollInterval, fetchStatus]);

  return { status, loading, error, refetch: fetchStatus };
}

/**
 * Hook for uploading documents
 */
export function useDocumentUpload() {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadedDocumentId, setUploadedDocumentId] = useState<string | null>(null);

  const uploadDocument = useCallback(
    async (file: File, workspaceId: string) => {
      try {
        setUploading(true);
        setError(null);

        const data = await api.uploadDocument(file, workspaceId);
        setUploadedDocumentId(data.id);
        return data;
      } catch (err) {
        let errorMessage = 'Document upload failed. Please try again.';
        
        if (err instanceof Error) {
          const msg = err.message.toLowerCase();
          if (msg.includes('413') || msg.includes('too large')) {
            errorMessage = 'File exceeds 50MB limit. Please upload a smaller file.';
          } else if (msg.includes('415') || msg.includes('unsupported')) {
            errorMessage = 'File type not supported. Use PDF, TXT, MD, or DOCX.';
          } else if (msg.includes('timeout')) {
            errorMessage = 'Upload timed out. Please check your connection and retry.';
          } else if (msg.includes('network')) {
            errorMessage = 'Network error. Please check your connection and retry.';
          } else {
            errorMessage = err.message;
          }
        }
        
        setError(errorMessage);
        throw err;
      } finally {
        setUploading(false);
      }
    },
    []
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
