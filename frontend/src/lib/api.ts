/**
 * Production-ready API Client for CogniFlow
 * Includes retry logic, timeout handling, error management, and request logging
 */

import {
  transformNoteFromAPI,
  transformWorkspaceFromAPI,
  transformDocumentFromAPI,
  transformPaginatedNotes,
  transformPaginatedWorkspaces,
  transformPaginatedDocuments,
  transformNotesArray,
  transformWorkspacesArray,
  transformQueryResponseFromAPI,
} from './transformers';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080';
const API_TIMEOUT = parseInt(import.meta.env.VITE_API_TIMEOUT || '30000'); // 30 seconds
const MAX_RETRIES = parseInt(import.meta.env.VITE_API_MAX_RETRIES || '3');
const RETRY_DELAY = parseInt(import.meta.env.VITE_API_RETRY_DELAY || '1000'); // ms

// Error Types
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: Record<string, any>
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export class ValidationError extends ApiError {
  constructor(message: string, details?: Record<string, any>) {
    super(422, 'VALIDATION_ERROR', message, details);
    this.name = 'ValidationError';
  }
}

export class AuthenticationError extends ApiError {
  constructor(message: string = 'Authentication required') {
    super(401, 'AUTHENTICATION_ERROR', message);
    this.name = 'AuthenticationError';
  }
}

export class AuthorizationError extends ApiError {
  constructor(message: string = 'Insufficient permissions') {
    super(403, 'AUTHORIZATION_ERROR', message);
    this.name = 'AuthorizationError';
  }
}

export class NotFoundError extends ApiError {
  constructor(message: string = 'Resource not found') {
    super(404, 'NOT_FOUND', message);
    this.name = 'NotFoundError';
  }
}

export class ServerError extends ApiError {
  constructor(message: string = 'Internal server error') {
    super(500, 'INTERNAL_ERROR', message);
    this.name = 'ServerError';
  }
}

// Types
export interface Document {
  id: string;
  workspace_id: string;
  title: string;
  source_type: string;
  source_metadata: Record<string, any>;
  storage_url?: string;
  status: 'pending' | 'processing' | 'indexed' | 'failed';
  token_count: number;
  chunk_count: number;
  processed_at?: string;
  created_at: string;
  updated_at?: string;
}

export interface Note {
  id: string;
  workspace_id?: string;
  user_id: string;
  title: string;
  content: string;
  summary?: string;
  note_type: string;
  tags: string[];
  connections: string[];
  ai_generated: boolean;
  confidence_score?: number;
  source_url?: string;
  word_count?: number;
  created_at: string;
  updated_at: string;
}

export interface Workspace {
  id: string;
  name: string;
  description?: string;
  owner_id: string;
  settings: Record<string, any>;
  created_at: string;
  updated_at?: string;
}

export interface SearchResult {
  chunk_id: string;
  document_id: string;
  document_title: string;
  content: string;
  highlight: string;
  source_type: string;
  chunk_index: number;
  created_at: string;
  interaction_count: number;
  similarity_score: number;
  recency_score: number;
  usage_score: number;
  final_score: number;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
  total: number;
  took_ms: number;
  cached?: boolean;
  search_log_id?: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: {
    id: string;
    email: string;
    full_name?: string;
  };
  workspaces?: Array<{
    id: string;
    name: string;
    role: 'owner' | 'admin' | 'member' | 'viewer';
  }>;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

// Request/Response Logging
class RequestLogger {
  private logs: Array<{
    timestamp: number;
    method: string;
    endpoint: string;
    status?: number;
    duration: number;
    error?: string;
  }> = [];

  private maxLogs = 100;

  log(
    method: string,
    endpoint: string,
    status: number | undefined,
    duration: number,
    error?: string
  ) {
    this.logs.push({
      timestamp: Date.now(),
      method,
      endpoint,
      status,
      duration,
      error,
    });

    // Keep only recent logs
    if (this.logs.length > this.maxLogs) {
      this.logs = this.logs.slice(-this.maxLogs);
    }

    // Log to console in development
    if (import.meta.env.DEV) {
      const statusColor = status && status >= 400 ? 'color: red' : 'color: green';
      console.log(
        `%c${method} ${endpoint} ${status || '?'}`,
        statusColor,
        `${duration}ms`,
        error ? error : ''
      );
    }
  }

  getLogs() {
    return this.logs;
  }

  clear() {
    this.logs = [];
  }
}

// Main API Client
class ApiClient {
  private baseUrl: string;
  private token: string | null;
  private logger: RequestLogger;
  private abortControllers: Map<string, AbortController> = new Map();

  constructor(baseUrl: string = API_BASE_URL) {
    this.baseUrl = baseUrl;
    this.token = localStorage.getItem('token');
    this.logger = new RequestLogger();
  }

  /**
   * Set authentication token
   */
  setToken(token: string): void {
    this.token = token;
    localStorage.setItem('token', token);
  }

  /**
   * Clear authentication token
   */
  clearToken(): void {
    this.token = null;
    localStorage.removeItem('token');
  }

  /**
   * Get current token
   */
  getToken(): string | null {
    return this.token;
  }

  /**
   * Check if user is authenticated
   */
  isAuthenticated(): boolean {
    return this.token !== null;
  }

  /**
   * Get request logs
   */
  getLogs() {
    return this.logger.getLogs();
  }

  private mergeAbortSignals(
    timeoutController: AbortController,
    externalSignal?: AbortSignal | null
  ): AbortSignal {
    if (!externalSignal) {
      return timeoutController.signal;
    }

    if (externalSignal.aborted) {
      timeoutController.abort();
      return timeoutController.signal;
    }

    const mergedController = new AbortController();
    const forwardAbort = () => {
      if (!mergedController.signal.aborted) {
        mergedController.abort();
      }
      timeoutController.abort();
      externalSignal.removeEventListener('abort', onExternalAbort);
      timeoutController.signal.removeEventListener('abort', onTimeoutAbort);
    };
    const onExternalAbort = () => forwardAbort();
    const onTimeoutAbort = () => forwardAbort();

    externalSignal.addEventListener('abort', onExternalAbort, { once: true });
    timeoutController.signal.addEventListener('abort', onTimeoutAbort, { once: true });

    return mergedController.signal;
  }

  /**
   * Retry logic with exponential backoff
   */
  private async retryRequest<T>(
    fetcher: () => Promise<T>,
    endpoint: string,
    maxRetries: number = MAX_RETRIES
  ): Promise<T> {
    let lastError: any;

    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        return await fetcher();
      } catch (error) {
        lastError = error;

        // Don't retry on client errors (4xx) except 408, 429, and 503
        if (error instanceof ApiError) {
          if (
            error.status < 500 &&
            error.status !== 408 &&
            error.status !== 429
          ) {
            throw error;
          }
        }

        // Don't retry on last attempt
        if (attempt === maxRetries) break;

        // Exponential backoff
        const delay = RETRY_DELAY * Math.pow(2, attempt - 1);
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }

    throw lastError;
  }

  /**
   * Core request method with timeout and error handling
   */
  private async request<T>(
    endpoint: string,
    options: RequestInit & { timeout?: number; retries?: boolean } = {}
  ): Promise<T> {
    const startTime = performance.now();
    const {
      timeout = API_TIMEOUT,
      retries = true,
      signal: externalSignal,
      ...fetchOptions
    } = options;

    const abortController = new AbortController();
    const timeoutId = setTimeout(() => abortController.abort(), timeout);
    const requestKey = `${options.method || 'GET'} ${endpoint}`;
    const requestSignal = this.mergeAbortSignals(abortController, externalSignal);

    try {
      this.abortControllers.set(requestKey, abortController);

      const headers: Record<string, string> = {};
      
      // Only set Content-Type for non-FormData requests
      // FormData should not have Content-Type set - browser will set multipart/form-data
      if (!(fetchOptions.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
      }
      
      // Merge with any provided headers
      Object.assign(headers, fetchOptions.headers as Record<string, string>);

      if (this.token) {
        headers['Authorization'] = `Bearer ${this.token}`;
      }

      const fetcher = async () => {
        const url = `${this.baseUrl}${endpoint}`;
        const response = await fetch(url, {
          ...fetchOptions,
          headers,
          signal: requestSignal,
        });

        return this.handleResponse<T>(response);
      };

      const result = retries ? await this.retryRequest(fetcher, endpoint) : await fetcher();
      const duration = performance.now() - startTime;
      this.logger.log(options.method || 'GET', endpoint, 200, duration);

      return result;
    } catch (error) {
      clearTimeout(timeoutId);
      const duration = performance.now() - startTime;

      if (error instanceof Error && error.name === 'AbortError') {
        const timeoutError = new ServerError(`Request timeout after ${timeout}ms`);
        this.logger.log(options.method || 'GET', endpoint, undefined, duration, timeoutError.message);
        throw timeoutError;
      }

      if (error instanceof ApiError) {
        this.logger.log(options.method || 'GET', endpoint, error.status, duration, error.message);
        throw error;
      }

      const genericError = new ServerError(
        error instanceof Error ? error.message : 'Unknown error'
      );
      this.logger.log(options.method || 'GET', endpoint, 500, duration, genericError.message);
      throw genericError;
    } finally {
      clearTimeout(timeoutId);
      this.abortControllers.delete(requestKey);
    }
  }

  /**
   * Handle API responses
   */
  private async handleResponse<T>(response: Response): Promise<T> {
    const contentType = response.headers.get('content-type');
    let body: any;

    if (contentType?.includes('application/json')) {
      body = await response.json().catch(() => ({}));
    } else {
      body = await response.text();
    }

    if (!response.ok) {
      const error = body?.error || { code: 'UNKNOWN_ERROR', message: 'Unknown error' };
      const message = error.message || body.detail || `HTTP ${response.status}`;

      switch (response.status) {
        case 400:
          throw new ValidationError(message, error.metadata);
        case 401:
          this.clearToken(); // Clear invalid token
          throw new AuthenticationError(message);
        case 403:
          throw new AuthorizationError(message);
        case 404:
          throw new NotFoundError(message);
        case 422:
          throw new ValidationError(message, error.metadata);
        case 429:
          throw new ServerError('Rate limited - please try again later');
        case 500:
        case 502:
        case 503:
        case 504:
          throw new ServerError(message);
        default:
          throw new ApiError(response.status, error.code || 'UNKNOWN', message);
      }
    }

    return body.items ? body : body;
  }

  /**
   * Cancel an ongoing request
   */
  cancelRequest(endpoint: string, method: string = 'GET'): void {
    const key = `${method} ${endpoint}`;
    const controller = this.abortControllers.get(key);
    if (controller) {
      controller.abort();
      this.abortControllers.delete(key);
    }
  }

  // ==================== Auth Endpoints ====================

  async register(credentials: {
    email: string;
    password: string;
    full_name?: string;
  }): Promise<TokenResponse> {
    return this.request('/auth/register', {
      method: 'POST',
      body: JSON.stringify(credentials),
    });
  }

  async login(credentials: {
    email: string;
    password: string;
  }): Promise<TokenResponse> {
    return this.request('/auth/login', {
      method: 'POST',
      body: JSON.stringify(credentials),
    });
  }

  async logout(): Promise<void> {
    this.clearToken();
  }

  async refresh(): Promise<TokenResponse> {
    return this.request('/auth/refresh', { method: 'POST' });
  }

  // ==================== Health Check ====================

  async health(): Promise<{ status: string; version: string; environment: string }> {
    return this.request('/health', { retries: false });
  }

  // ==================== Documents Endpoints ====================

  async uploadDocument(
    file: File,
    workspaceId: string,
    options?: { signal?: AbortSignal }
  ): Promise<Document> {
    const formData = new FormData();
    formData.append('file', file);

    // workspace_id as query parameter (backend expects Query parameter)
    return this.request(`/documents/upload?workspace_id=${encodeURIComponent(workspaceId)}`, {
      method: 'POST',
      body: formData,
      signal: options?.signal,
    });
  }

  async listDocuments(
    workspaceId: string,
    params?: {
      status?: string;
      page?: number;
      page_size?: number;
      sort_by?: string;
      sort_order?: string;
    }
  ): Promise<ReturnType<typeof transformPaginatedDocuments>> {
    const queryParams = new URLSearchParams({ workspace_id: workspaceId });
    if (params?.status) queryParams.append('status', params.status);
    if (params?.page) queryParams.append('page', params.page.toString());
    if (params?.page_size) queryParams.append('page_size', params.page_size.toString());
    if (params?.sort_by) queryParams.append('sort_by', params.sort_by);
    if (params?.sort_order) queryParams.append('sort_order', params.sort_order);

    const response = await this.request(`/documents?${queryParams}`);
    return transformPaginatedDocuments(response);
  }

  async getDocument(documentId: string): Promise<Document> {
    return this.request(`/documents/${documentId}`);
  }

  /**
   * Get detailed ingestion status for a document.
   * CRITICAL FIX: Provides a fallback to fetch actual status from backend
   * when event streaming misses updates.
   */
  async getIngestionStatus(
    documentId: string
  ): Promise<{
    document_id: string;
    title: string;
    source_type: string;
    status: 'pending' | 'processing' | 'indexed' | 'failed';
    progress: {
      chunks_created: number;
      embeddings_created: number;
      total_tokens: number;
    };
    logs: Array<{
      stage: string;
      status: string;
      duration_ms: number;
      timestamp: string;
    }>;
    created_at: string;
    updated_at: string | null;
    error?: string;
  }> {
    return this.request(`/ingestion/status/${documentId}`);
  }

  /**
   * Get workspace-level ingestion statistics.
   * CRITICAL FIX: Provides accurate chunk/embedding counts from database
   * when event streaming is out of sync.
   */
  async getWorkspaceIngestionStatus(
    workspaceId: string,
    statusFilter?: string
  ): Promise<{
    workspace_id: string;
    total_documents: number;
    status_breakdown: Record<string, number>;
    aggregated_stats: {
      total_chunks: number;
      total_tokens: number;
      recent_activities_24h: number;
    };
    document_statuses: Array<{
      document_id: string;
      title: string;
      source_type: string;
      status: string;
      progress: {
        chunks_created: number;
        embeddings_created: number;
        total_tokens: number;
      };
      created_at: string;
      updated_at: string | null;
    }>;
  }> {
    const queryParams = new URLSearchParams();
    if (statusFilter) queryParams.append('status_filter', statusFilter);
    return this.request(
      `/ingestion/workspace/${workspaceId}/status${queryParams.toString() ? `?${queryParams}` : ''}`
    );
  }

  async deleteDocument(documentId: string): Promise<void> {
    await this.request(`/documents/${documentId}`, { method: 'DELETE' });
  }

  // ==================== Notes Endpoints ====================

  async createNote(note: {
    title: string;
    content: string;
    workspace_id?: string;
    tags?: string[];
    source_url?: string;
    note_type?: string;
  }): Promise<Note> {
    return this.request('/notes', {
      method: 'POST',
      body: JSON.stringify(note),
    });
  }

  async listNotes(params?: {
    workspace_id?: string;
    search?: string;
    tags?: string[];
    page?: number;
    page_size?: number;
  }): Promise<PaginatedResponse<Note>> {
    const queryParams = new URLSearchParams();
    if (params?.workspace_id) queryParams.append('workspace_id', params.workspace_id);
    if (params?.search) queryParams.append('search', params.search);
    if (params?.tags) params.tags.forEach((tag) => queryParams.append('tags', tag));
    if (params?.page) queryParams.append('page', params.page.toString());
    if (params?.page_size) queryParams.append('page_size', params.page_size.toString());

    const response = await this.request(`/notes?${queryParams}`);
    return transformPaginatedNotes(response);
  }

  async getNote(noteId: string): Promise<Note> {
    return this.request(`/notes/${noteId}`);
  }

  async updateNote(
    noteId: string,
    updates: Partial<{
      title: string;
      content: string;
      tags: string[];
      connections: string[];
      note_type: string;
    }>
  ): Promise<Note> {
    return this.request(`/notes/${noteId}`, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    });
  }

  async deleteNote(noteId: string): Promise<void> {
    await this.request(`/notes/${noteId}`, { method: 'DELETE' });
  }

  async getWorkspaceNotes(
    workspaceId: string,
    params?: {
      search?: string;
      tags?: string[];
      page?: number;
      page_size?: number;
    }
  ): Promise<PaginatedResponse<Note>> {
    const queryParams = new URLSearchParams();
    if (params?.search) queryParams.append('search', params.search);
    if (params?.tags) params.tags.forEach((tag) => queryParams.append('tags', tag));
    if (params?.page) queryParams.append('page', params.page.toString());
    if (params?.page_size) queryParams.append('page_size', params.page_size.toString());

    const response = await this.request(
      `/notes/workspace/${workspaceId}${queryParams.toString() ? `?${queryParams}` : ''}`
    );
    return transformPaginatedNotes(response);
  }

  // ==================== Workspaces Endpoints ====================

  async createWorkspace(workspace: {
    name: string;
    description?: string;
  }): Promise<Workspace> {
    return this.request('/workspaces', {
      method: 'POST',
      body: JSON.stringify(workspace),
    });
  }

  async listWorkspaces(): Promise<Workspace[]> {
    const response: any = await this.request('/workspaces');
    return Array.isArray(response) ? response : response.items || [];
  }

  async getWorkspace(workspaceId: string): Promise<Workspace> {
    return this.request(`/workspaces/${workspaceId}`);
  }

  async updateWorkspace(
    workspaceId: string,
    updates: Partial<{
      name: string;
      description: string;
      settings: Record<string, any>;
    }>
  ): Promise<Workspace> {
    return this.request(`/workspaces/${workspaceId}`, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    });
  }

  async getWorkspaceStats(workspaceId: string): Promise<{
    documents: { total: number; indexed: number; processing: number };
    vectors: number;
  }> {
    return this.request(`/workspaces/${workspaceId}/stats`);
  }

  // ==================== Search Endpoints ====================

  async search(
    query: string,
    workspaceId: string,
    options?: {
      limit?: number;
      filters?: {
        tags?: string[];
        date_from?: string;
        date_to?: string;
        source_type?: string;
      };
    }
  ): Promise<SearchResponse> {
    const queryParams = new URLSearchParams({
      workspace_id: workspaceId,
      q: query,
      limit: String(options?.limit || 10),
    });

    if (options?.filters?.tags?.length) {
      queryParams.append('tags', options.filters.tags.join(','));
    }
    if (options?.filters?.date_from) {
      queryParams.append('date_from', options.filters.date_from);
    }
    if (options?.filters?.date_to) {
      queryParams.append('date_to', options.filters.date_to);
    }
    if (options?.filters?.source_type) {
      queryParams.append('source_type', options.filters.source_type);
    }

    return this.request(`/search/semantic?${queryParams.toString()}`);
  }

  async logSearchClick(
    workspaceId: string,
    searchLogId: string,
    chunkId: string
  ): Promise<{ status: string; clicked_count: number }> {
    const queryParams = new URLSearchParams({
      workspace_id: workspaceId,
      search_log_id: searchLogId,
      chunk_id: chunkId,
    });

    return this.request(`/search/log-click?${queryParams.toString()}`, {
      method: 'POST',
    });
  }

  // ==================== Audit Endpoints ====================

  async getAuditLogs(
    workspaceId: string,
    filters?: {
      action?: string;
      entity_type?: string;
      limit?: number;
      offset?: number;
    }
  ): Promise<PaginatedResponse<any>> {
    const queryParams = new URLSearchParams();
    if (filters?.action) queryParams.append('action', filters.action);
    if (filters?.entity_type) queryParams.append('entity_type', filters.entity_type);
    if (filters?.limit) queryParams.append('limit', filters.limit.toString());
    if (filters?.offset) queryParams.append('offset', filters.offset.toString());

    return this.request(`/audit/workspace/${workspaceId}?${queryParams}`);
  }

  // ==================== Knowledge Graph Endpoints ====================

  async getKnowledgeGraph(workspaceId: string): Promise<{
    nodes: Array<{ id: string; type: string; label: string; value: number; metadata: any }>;
    edges: Array<{ source: string; target: string; type: string; weight: number }>;
    stats: Record<string, number>;
  }> {
    return this.request(`/knowledge-graph/${workspaceId}`);
  }

  // ==================== Auth Additional Endpoints ====================

  async getCurrentUser(): Promise<{ id: string; email: string; full_name?: string; is_active: boolean }> {
    return this.request('/auth/me');
  }

  async changePassword(oldPassword: string, newPassword: string): Promise<void> {
    await this.request('/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    });
  }

  async inviteToWorkspace(workspaceId: string, email: string, role: 'owner' | 'admin' | 'member' | 'viewer'): Promise<any> {
    return this.request(`/auth/workspaces/${workspaceId}/invite`, {
      method: 'POST',
      body: JSON.stringify({ email, role }),
    });
  }

  // ==================== Document Additional Endpoints ====================

  async getDocumentChunks(documentId: string): Promise<Array<{
    id: string;
    document_id: string;
    text: string;
    token_count: number;
    index: number;
    context_before?: string;
    context_after?: string;
  }>> {
    return this.request(`/documents/${documentId}/chunks`);
  }

  // ==================== Ingestion Endpoints ====================

  async retryIngestion(documentId: string): Promise<{ status: string; new_status: string }> {
    return this.request(`/ingestion/documents/${documentId}/retry`, {
      method: 'POST',
    });
  }

  async getIngestionLogs(documentId: string): Promise<Array<{
    stage: string;
    status: string;
    duration_ms: number;
    error?: string;
    timestamp: string;
  }>> {
    return this.request(`/ingestion/logs/${documentId}`);
  }

  // ==================== Enhanced Query/Search Endpoints ====================

  async query(
    query: string,
    workspaceId: string,
    options?: {
      limit?: number;
      model?: string;
      signal?: AbortSignal;
    }
  ): Promise<{
    query_id: string;
    answer_id: string;
    answer: string;
    sources: Array<{
      chunk_id: string;
      document_id: string;
      document_title: string;
      similarity: number;
    }>;
    confidence: number;
    confidence_factors: Record<string, number>;
    model_used: string;
    tokens_used: number;
    response_time_ms: number;
  }> {
    // RAG queries can take longer due to embedding generation + vector search + LLM generation
    // Timeout: 120 seconds (2 minutes) to allow for:
    // - Query embedding: ~5-10s
    // - Vector search: ~2-5s
    // - LLM generation with context: ~30-90s (depending on provider)
    // - Network/retry overhead: ~5-10s
    // - Cold start (if Ollama preload failed): ~60-80s additional
    const payload: Record<string, any> = {
      workspace_id: workspaceId,
      query,
      top_k: options?.limit || 5,
      include_sources: true,
    };

    if (options?.model) {
      payload.model = options.model;
    }

    return this.request(`/query`, {
      method: 'POST',
      body: JSON.stringify(payload),
      signal: options?.signal,
      timeout: 300000, // 300s for RAG queries (180s backend LLM + 30s retrieval + 90s buffer for slow Ollama)
    });
  }

  // ==================== STEP 7 & 8: Answer Feedback & Verification ====================

  async getAnswerStep7Format(answerId: string): Promise<{ answer: string; confidence: number; sources: Array<{ document_id: string; chunk_index: number; similarity: number }> }> {
    return this.request(`/answers/${answerId}/step7`);
  }

  async submitAnswerFeedback(
    answerId: string,
    status: 'verified' | 'rejected',
    comment?: string
  ): Promise<{ answer_id: string; feedback_status: string; chunks_updated: Array<{ chunk_id: string; document_id: string; old_weight: number; new_weight: number; accuracy: number }> }> {
    return this.request(`/answers/${answerId}/feedback`, {
      method: 'POST',
      body: JSON.stringify({ answer_id: answerId, status, comment }),
    });
  }

  async getChunkCredibilityScores(workspaceId: string, limit: number = 50): Promise<Array<{
    chunk_id: string;
    document_id: string;
    credibility_score: number;
    accuracy_rate: number;
    positive_feedback: number;
    negative_feedback: number;
    total_uses: number;
    updated_at?: string;
  }>> {
    return this.request(`/answers/${workspaceId}/credibility?limit=${limit}`);
  }

  async getModelEvaluationMetrics(workspaceId: string): Promise<{
    total_feedback: number;
    approved_count: number;
    rejected_count: number;
    approval_rate: number;
    rejection_rate: number;
    average_rating: number;
  }> {
    return this.request(`/answers/${workspaceId}/evaluation-metrics`);
  }

  // ==================== Workspace Member Endpoints ====================

  async getWorkspaceMembers(workspaceId: string): Promise<Array<{
    user_id: string;
    email: string;
    full_name?: string;
    role: string;
    joined_at: string;
  }>> {
    return this.request(`/workspaces/${workspaceId}/members`);
  }

  async removeMember(workspaceId: string, userId: string): Promise<void> {
    await this.request(`/workspaces/${workspaceId}/members/${userId}`, {
      method: 'DELETE',
    });
  }

  async deleteWorkspace(workspaceId: string): Promise<void> {
    await this.request(`/workspaces/${workspaceId}`, { method: 'DELETE' });
  }

  // ==================== Connector Endpoints ====================

  async createConnector(connector: {
    workspace_id: string;
    connector_type: string;
    access_token: string;
    config?: Record<string, any>;
  }): Promise<any> {
    return this.request('/connectors', {
      method: 'POST',
      body: JSON.stringify(connector),
    });
  }
}

// Export singleton instance
export const api = new ApiClient();

// React hook for API
export function useApi() {
  return api;
}

