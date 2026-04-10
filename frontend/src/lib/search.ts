/**
 * Semantic search service for TypeScript/React frontend
 * Handles communication with the backend semantic search API
 */

export interface SearchFilter {
  tags?: string[];
  date_from?: string;
  date_to?: string;
  source_type?: string;
}

export interface SemanticSearchResult {
  chunk_id: string;
  document_id: string;
  document_title: string;
  content: string;
  source_type: string;
  chunk_index: number;
  created_at: string;
  interaction_count: number;
  similarity_score: number;
  recency_score: number;
  usage_score: number;
  final_score: number;
  highlight: string;
}

export interface SemanticSearchResponse {
  query: string;
  results: SemanticSearchResult[];
  total: number;
  took_ms: number;
  cached: boolean;
  search_log_id?: string | null;
}

export interface TrendingSearch {
  query: string;
  search_count: number;
  unique_users: number;
}

export interface TrendingSearchResponse {
  trending: TrendingSearch[];
  period: string;
  workspace_id: string;
}

/**
 * Semantic search API client
 */
export class SemanticSearchClient {
  private apiUrl: string;
  workspaceId: string;

  constructor(apiUrl: string, workspaceId: string) {
    this.apiUrl = apiUrl;
    this.workspaceId = workspaceId;
  }

  /**
   * Get authorization headers with JWT token
   */
  private getHeaders(): HeadersInit {
    const token = localStorage.getItem('token');
    return {
      'Authorization': `Bearer ${token || ''}`,
      'Content-Type': 'application/json',
    };
  }

  /**
   * Perform semantic search
   * @param query Search query text
   * @param limit Number of results to return (1-50)
   * @param filters Optional search filters
   * @returns Search results with composite scoring
   */
  async search(
    query: string,
    limit: number = 10,
    filters?: SearchFilter
  ): Promise<SemanticSearchResponse> {
    if (!query || query.trim().length === 0) {
      throw new Error('Search query is required');
    }

    if (limit < 1 || limit > 50) {
      throw new Error('Limit must be between 1 and 50');
    }

    try {
      const params = new URLSearchParams({
        workspace_id: this.workspaceId,
        q: query,
        limit: limit.toString(),
      });

      // Add filter parameters
      if (filters) {
        if (filters.tags && filters.tags.length > 0) {
          params.append('tags', filters.tags.join(','));
        }
        if (filters.date_from) {
          params.append('date_from', filters.date_from);
        }
        if (filters.date_to) {
          params.append('date_to', filters.date_to);
        }
        if (filters.source_type) {
          params.append('source_type', filters.source_type);
        }
      }

      const response = await fetch(
        `${this.apiUrl}/search/semantic?${params.toString()}`,
        {
          method: 'GET',
          headers: this.getHeaders(),
        }
      );

      if (!response.ok) {
        throw new Error(`Search failed: ${response.statusText}`);
      }

      const data: SemanticSearchResponse = await response.json();
      return data;
    } catch (error) {
      console.error('Semantic search error:', error);
      throw error;
    }
  }

  /**
   * Get trending searches in workspace
   * @param limit Number of trends to return
   * @returns Trending searches
   */
  async getTrendingSearches(limit: number = 10): Promise<TrendingSearchResponse> {
    if (limit < 1 || limit > 50) {
      throw new Error('Limit must be between 1 and 50');
    }

    try {
      const params = new URLSearchParams({
        workspace_id: this.workspaceId,
        limit: limit.toString(),
      });

      const response = await fetch(
        `${this.apiUrl}/search/trending?${params.toString()}`,
        {
          method: 'GET',
          headers: this.getHeaders(),
        }
      );

      if (!response.ok) {
        throw new Error(`Failed to fetch trending searches: ${response.statusText}`);
      }

      const data: TrendingSearchResponse = await response.json();
      return data;
    } catch (error) {
      console.error('Error fetching trending searches:', error);
      throw error;
    }
  }

  /**
   * Log a click on a search result
   * @param searchLogId ID of the search log entry
   * @param chunkId ID of the chunk that was clicked
   */
  async logClick(searchLogId: string, chunkId: string): Promise<{ status: string; clicked_count: number }> {
    try {
      const params = new URLSearchParams({
        workspace_id: this.workspaceId,
        search_log_id: searchLogId,
        chunk_id: chunkId,
      });

      const response = await fetch(
        `${this.apiUrl}/search/log-click?${params.toString()}`,
        {
          method: 'POST',
          headers: this.getHeaders(),
        }
      );

      if (!response.ok) {
        throw new Error(`Failed to log click: ${response.statusText}`);
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error('Error logging search click:', error);
      // Don't throw - logging clicks shouldn't break the UI
      return { status: 'error', clicked_count: 0 };
    }
  }
}

// Singleton instance
let searchClientInstance: SemanticSearchClient | null = null;

/**
 * Create or get semantic search client instance
 * @param apiUrl Backend API URL
 * @param workspaceId Workspace UUID
 * @returns Semantic search client
 */
export function createSemanticSearchClient(
  apiUrl: string,
  workspaceId: string
): SemanticSearchClient {
  if (!searchClientInstance) {
    searchClientInstance = new SemanticSearchClient(apiUrl, workspaceId);
  } else {
    // Update workspace if changed
    searchClientInstance.workspaceId = workspaceId;
  }
  return searchClientInstance;
}

/**
 * Get existing semantic search client instance
 * @throws Error if client not initialized
 */
export function getSemanticSearchClient(): SemanticSearchClient {
  if (!searchClientInstance) {
    throw new Error('Semantic search client not initialized. Call createSemanticSearchClient first.');
  }
  return searchClientInstance;
}

/**
 * Debounce search function to avoid excessive API calls
 * @param fn Function to debounce
 * @param delayMs Delay in milliseconds
 */
export function debounce<T extends (...args: any[]) => Promise<any>>(
  fn: T,
  delayMs: number
): (...args: Parameters<T>) => Promise<ReturnType<T>> {
  let timeoutId: NodeJS.Timeout | null = null;
  let lastResult: ReturnType<T> | null = null;

  return async (...args: Parameters<T>): Promise<ReturnType<T>> => {
    return new Promise((resolve) => {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }

      timeoutId = setTimeout(async () => {
        lastResult = await fn(...args);
        resolve(lastResult);
      }, delayMs);
    });
  };
}

/**
 * Extract snippet from content around search term
 * @param content Full content text
 * @param query Search query
 * @param contextLength Characters of context before/after match
 */
export function extractSnippet(
  content: string,
  query: string,
  contextLength: number = 80
): string {
  if (!content || content.length === 0) {
    return '';
  }

  const queryLower = query.toLowerCase();
  const contentLower = content.toLowerCase();
  const index = contentLower.indexOf(queryLower);

  if (index === -1) {
    return content.slice(0, contextLength * 2) + (content.length > contextLength * 2 ? '...' : '');
  }

  const start = Math.max(0, index - contextLength);
  const end = Math.min(content.length, index + query.length + contextLength);

  const prefix = start > 0 ? '...' : '';
  const suffix = end < content.length ? '...' : '';

  return prefix + content.slice(start, end) + suffix;
}

/**
 * Highlight search terms in text with bold formatting
 * @param text Text to highlight
 * @param terms Terms to highlight
 */
export function highlightTerms(text: string, terms: string[]): string {
  if (!terms || terms.length === 0) {
    return text;
  }

  let result = text;
  for (const term of terms) {
    const regex = new RegExp(`(${term})`, 'gi');
    result = result.replace(regex, '**$1**');
  }
  return result;
}

/**
 * Parse composite score into human-readable feedback
 * @param finalScore Final composite score (0-1)
 * @param similarityScore Semantic similarity component
 * @param recencyScore Recency component
 * @param usageScore Usage component
 */
export function scoreToReadable(
  finalScore: number,
  similarityScore: number,
  recencyScore: number,
  usageScore: number
): {
  label: string;
  color: string;
  components: string[];
} {
  const components = [
    `Similarity: ${(similarityScore * 100).toFixed(0)}%`,
    `Recency: ${(recencyScore * 100).toFixed(0)}%`,
    `Usage: ${(usageScore * 100).toFixed(0)}%`,
  ];

  if (finalScore >= 0.9) {
    return { label: 'Excellent Match', color: '#10b981', components };
  } else if (finalScore >= 0.7) {
    return { label: 'Very Good Match', color: '#3b82f6', components };
  } else if (finalScore >= 0.5) {
    return { label: 'Good Match', color: '#f59e0b', components };
  } else if (finalScore >= 0.3) {
    return { label: 'Fair Match', color: '#ef4444', components };
  } else {
    return { label: 'Poor Match', color: '#6b7280', components };
  }
}
