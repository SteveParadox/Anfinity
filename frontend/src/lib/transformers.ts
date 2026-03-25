/**
 * Centralized Data Transformers for API Responses
 * 
 * Handles snake_case → camelCase conversion and type coercion
 * Ensures consistent data format across the application
 */

import type {
  Note, Workspace, Document, 
  AIInsight, SearchResult, PricingPlan,
} from '@/types';

/**
 * Transform note from API (snake_case) to frontend (camelCase)
 */
export function transformNoteFromAPI(note: any): Note {
  const createdAtStr = note.created_at || note.createdAt;
  const updatedAtStr = note.updated_at || note.updatedAt;
  
  return {
    id: note.id,
    title: note.title,
    content: note.content,
    summary: note.summary || undefined,
    tags: note.tags || [],
    connections: note.connections || [],
    userId: note.user_id || note.userId,
    workspaceId: note.workspace_id || note.workspaceId,
    createdAt: createdAtStr ? new Date(createdAtStr) : new Date(),
    updatedAt: updatedAtStr ? new Date(updatedAtStr) : new Date(),
    confidence: note.confidence_score || note.confidence,
    source: note.source_url || note.source,
    type: note.note_type || note.type || 'note',
    word_count: note.word_count,
  };
}

/**
 * Transform workspace from API (snake_case) to frontend (camelCase)
 */
export function transformWorkspaceFromAPI(workspace: any): Workspace {
  const createdAtStr = workspace.created_at || workspace.createdAt;
  const updatedAtStr = workspace.updated_at || workspace.updatedAt;
  
  return {
    id: workspace.id,
    name: workspace.name,
    description: workspace.description || '',
    members: workspace.members || [],
    createdAt: createdAtStr ? new Date(createdAtStr) : new Date(),
    updatedAt: updatedAtStr ? new Date(updatedAtStr) : new Date(),
  };
}

/**
 * Transform document from API to frontend format
 */
export function transformDocumentFromAPI(doc: any): Document {
  const createdAtStr = doc.created_at || doc.createdAt;
  const updatedAtStr = doc.updated_at || doc.updatedAt;
  
  return {
    id: doc.id,
    workspaceId: doc.workspace_id || doc.workspaceId,
    title: doc.title,
    sourceType: (doc.source_type || doc.sourceType) as any,
    status: doc.status as 'pending' | 'processing' | 'indexed' | 'failed',
    tokenCount: doc.token_count || doc.tokenCount || 0,
    chunkCount: doc.chunk_count || doc.chunkCount || 0,
    storageUrl: doc.storage_url || doc.storageUrl,
    createdAt: createdAtStr ? new Date(createdAtStr) : new Date(),
    updatedAt: updatedAtStr ? new Date(updatedAtStr) : new Date(),
  };
}

/**
 * Transform query response from API
 */
export interface QueryResponse {
  query_id: string;
  query: string;
  answer: string;
  confidence: number;
  confidence_factors: {
    similarity_avg: number;
    document_diversity: number;
    source_coverage: number;
    chunks_retrieved: number;
    unique_documents: number;
  };
  sources: Array<{
    chunk_id: string;
    document_id: string;
    document_title: string;
    text: string;
    similarity: number;
  }>;
  model_used: string;
  tokens_used: number;
  response_time_ms: number;
}

export function transformQueryResponseFromAPI(response: any): QueryResponse {
  return {
    query_id: response.query_id || response.queryId,
    query: response.query,
    answer: response.answer,
    confidence: response.confidence,
    confidence_factors: {
      similarity_avg: response.confidence_factors?.similarity_avg || 
                      response.confidenceFactors?.similarityAvg || 0,
      document_diversity: response.confidence_factors?.document_diversity || 
                          response.confidenceFactors?.documentDiversity || 0,
      source_coverage: response.confidence_factors?.source_coverage || 
                       response.confidenceFactors?.sourceCoverage || 0,
      chunks_retrieved: response.confidence_factors?.chunks_retrieved || 
                        response.confidenceFactors?.chunksRetrieved || 0,
      unique_documents: response.confidence_factors?.unique_documents || 
                        response.confidenceFactors?.uniqueDocuments || 0,
    },
    sources: (response.sources || []).map((source: any) => ({
      chunk_id: source.chunk_id || source.chunkId,
      document_id: source.document_id || source.documentId,
      document_title: source.document_title || source.documentTitle,
      text: source.text,
      similarity: source.similarity,
    })),
    model_used: response.model_used || response.modelUsed,
    tokens_used: response.tokens_used || response.tokensUsed,
    response_time_ms: response.response_time_ms || response.responseTimeMs,
  };
}

/**
 * Transform paginated notes response
 */
export function transformPaginatedNotes(response: any) {
  return {
    items: (response.items || []).map(transformNoteFromAPI),
    total: response.total || 0,
    page: response.page || 1,
    page_size: response.page_size || response.pageSize || 20,
  };
}

/**
 * Transform paginated workspaces response
 */
export function transformPaginatedWorkspaces(response: any) {
  return {
    items: (response.items || []).map(transformWorkspaceFromAPI),
    total: response.total || 0,
    page: response.page || 1,
    page_size: response.page_size || response.pageSize || 20,
  };
}

/**
 * Transform paginated documents response
 */
export function transformPaginatedDocuments(response: any) {
  return {
    items: (response.items || []).map(transformDocumentFromAPI),
    total: response.total || 0,
    page: response.page || 1,
    page_size: response.page_size || response.pageSize || 20,
  };
}

/**
 * Transform array of notes
 */
export function transformNotesArray(notes: any[]): Note[] {
  return (notes || []).map(transformNoteFromAPI);
}

/**
 * Transform array of workspaces
 */
export function transformWorkspacesArray(workspaces: any[]): Workspace[] {
  return (workspaces || []).map(transformWorkspaceFromAPI);
}

/**
 * Transform array of documents
 */
export function transformDocumentsArray(documents: any[]): Document[] {
  return (documents || []).map(transformDocumentFromAPI);
}

/**
 * Create derived insights from notes
 */
export function deriveInsightsFromNotes(notes: Note[]): AIInsight[] {
  const notesCount = notes.length;
  const recentNotes = notes.slice(0, 5);
  const tagsUsed = new Set<string>();
  
  notes.forEach(note => {
    (note.tags || []).forEach(tag => tagsUsed.add(tag));
  });

  return [
    {
      id: '1',
      content: `Your workspace has ${notesCount} notes in the knowledge base.`,
      sources: [],
      confidence: 0.95,
      createdAt: new Date(),
      type: 'suggestion',
    },
    {
      id: '2',
      content: 'Knowledge base is actively growing with recent additions.',
      sources: recentNotes.map(n => n.id),
      confidence: 0.87,
      createdAt: new Date(),
      type: 'trend',
    },
    ...(tagsUsed.size > 0 ? [{
      id: '3',
      content: `Using ${tagsUsed.size} unique tags to organize knowledge (${Array.from(tagsUsed).slice(0, 3).join(', ')}${tagsUsed.size > 3 ? '...' : ''})`,
      sources: [],
      confidence: 0.8,
      createdAt: new Date(),
      type: 'summary' as const,
    }] : []),
  ];
}

/**
 * Safe transformation with error handling
 */
export function safeTransform<T>(
  data: any,
  transformer: (data: any) => T,
  fallback: T
): T {
  try {
    return transformer(data);
  } catch (error) {
    console.error('Transformation error:', error);
    return fallback;
  }
}

/**
 * Batch safe transformation
 */
export function batchSafeTransform<T>(
  data: any[],
  transformer: (item: any) => T,
  fallback: T
): T[] {
  return (data || []).map((item, index) => {
    try {
      return transformer(item);
    } catch (error) {
      console.error(`Transformation error at index ${index}:`, error);
      return fallback;
    }
  });
}
