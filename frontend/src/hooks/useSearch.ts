/**
 * Custom React hooks for semantic search functionality
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type {
  SemanticSearchResponse,
  SemanticSearchResult,
  SearchFilter,
  TrendingSearchResponse,
} from '../lib/search';
import {
  SemanticSearchClient,
  debounce,
} from '../lib/search';

/**
 * Hook for performing semantic searches
 * @param apiUrl Backend API URL
 * @param workspaceId Current workspace UUID
 * @param debounceMs Debounce delay in milliseconds
 */
export function useSemanticSearch(apiUrl: string, workspaceId: string, debounceMs: number = 300) {
  const [query, setQuery] = useState<string>('');
  const [results, setResults] = useState<SemanticSearchResult[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [took_ms, setTookMs] = useState<number>(0);
  const [cached, setCached] = useState<boolean>(false);
  const [resultCount, setResultCount] = useState<number>(0);

  const clientRef = useRef<SemanticSearchClient>(
    new SemanticSearchClient(apiUrl, workspaceId)
  );

  useEffect(() => {
    clientRef.current = new SemanticSearchClient(apiUrl, workspaceId);
  }, [apiUrl, workspaceId]);

  // Debounced search function
  const performSearch = useCallback(
    debounce(async (searchQuery: string, filters?: SearchFilter) => {
      if (!searchQuery || searchQuery.trim().length === 0) {
        setResults([]);
        setResultCount(0);
        return;
      }

      setLoading(true);
      setError(null);

      try {
        const response: SemanticSearchResponse = await clientRef.current.search(
          searchQuery,
          10,
          filters
        );

        setResults(response.results);
        setTookMs(response.took_ms);
        setCached(response.cached);
        setResultCount(response.total);
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : 'Search failed';
        setError(errorMessage);
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, debounceMs),
    [debounceMs]
  );

  const search = useCallback(
    (newQuery: string, filters?: SearchFilter) => {
      setQuery(newQuery);
      performSearch(newQuery, filters);
    },
    [performSearch]
  );

  const clearSearch = useCallback(() => {
    setQuery('');
    setResults([]);
    setError(null);
    setCached(false);
  }, []);

  return {
    query,
    results,
    loading,
    error,
    took_ms,
    cached,
    resultCount,
    search,
    clearSearch,
    setQuery,
  };
}

/**
 * Hook for getting trending searches
 * @param apiUrl Backend API URL
 * @param workspaceId Current workspace UUID
 * @param limit Number of trends to fetch
 * @param autoFetch Whether to automatically fetch on mount
 */
export function useTrendingSearches(
  apiUrl: string,
  workspaceId: string,
  limit: number = 10,
  autoFetch: boolean = true
) {
  const [trending, setTrending] = useState<any[]>([]);
  const [loading, setLoading] = useState<boolean>(autoFetch);
  const [error, setError] = useState<string | null>(null);

  const clientRef = useRef<SemanticSearchClient>(
    new SemanticSearchClient(apiUrl, workspaceId)
  );

  useEffect(() => {
    clientRef.current = new SemanticSearchClient(apiUrl, workspaceId);
  }, [apiUrl, workspaceId]);

  const fetchTrending = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const response: TrendingSearchResponse = await clientRef.current.getTrendingSearches(limit);
      setTrending(response.trending);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to fetch trending searches';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    if (autoFetch) {
      fetchTrending();
    }
  }, [autoFetch, fetchTrending]);

  return {
    trending,
    loading,
    error,
    refresh: fetchTrending,
  };
}

/**
 * Hook for logging search result clicks
 * @param apiUrl Backend API URL
 * @param workspaceId Current workspace UUID
 */
export function useSearchClick(apiUrl: string, workspaceId: string) {
  const clientRef = useRef<SemanticSearchClient>(
    new SemanticSearchClient(apiUrl, workspaceId)
  );

  useEffect(() => {
    clientRef.current = new SemanticSearchClient(apiUrl, workspaceId);
  }, [apiUrl, workspaceId]);

  const logClick = useCallback(
    async (searchLogId: string, chunkId: string) => {
      try {
        await clientRef.current.logClick(searchLogId, chunkId);
      } catch (error) {
        console.error('Failed to log search click:', error);
        // Don't throw - logging shouldn't break user interaction
      }
    },
    []
  );

  return { logClick };
}

/**
 * Hook for search filters state management
 */
export function useSearchFilters() {
  const [filters, setFilters] = useState<SearchFilter>({});

  const updateFilter = useCallback((key: keyof SearchFilter, value: any) => {
    setFilters((prev) => ({
      ...prev,
      [key]: value || undefined,
    }));
  }, []);

  const clearFilters = useCallback(() => {
    setFilters({});
  }, []);

  const hasActiveFilters = useMemo(() => {
    return Object.values(filters).some((v) => v !== undefined && v !== null);
  }, [filters]);

  return {
    filters,
    updateFilter,
    clearFilters,
    hasActiveFilters,
  };
}

/**
 * Hook for managing search history
 * @param maxItems Maximum number of history items to keep
 */
export function useSearchHistory(maxItems: number = 10) {
  const [history, setHistory] = useState<string[]>([]);

  const addSearch = useCallback((query: string) => {
    if (query.trim().length === 0) return;

    setHistory((prev) => {
      // Remove duplicate if exists
      const filtered = prev.filter((q) => q !== query);
      // Add new query to front and limit size
      return [query, ...filtered].slice(0, maxItems);
    });
  }, [maxItems]);

  const removeSearch = useCallback((query: string) => {
    setHistory((prev) => prev.filter((q) => q !== query));
  }, []);

  const clearHistory = useCallback(() => {
    setHistory([]);
  }, []);

  return {
    history,
    addSearch,
    removeSearch,
    clearHistory,
  };
}

/**
 * Hook for search analytics and insights
 */
export function useSearchAnalytics(results: SemanticSearchResult[]) {
  const analytics = useMemo(() => {
    if (results.length === 0) {
      return {
        averageSimilarity: 0,
        averageRecency: 0,
        averageUsage: 0,
        averageFinalScore: 0,
        topSourceType: null,
        scoreDistribution: [0, 0, 0, 0, 0],
      };
    }

    const averageSimilarity = results.reduce((sum, r) => sum + r.similarity_score, 0) / results.length;
    const averageRecency = results.reduce((sum, r) => sum + r.recency_score, 0) / results.length;
    const averageUsage = results.reduce((sum, r) => sum + r.usage_score, 0) / results.length;
    const averageFinalScore = results.reduce((sum, r) => sum + r.final_score, 0) / results.length;

    // Find most common source type
    const sourceTypeCounts: Record<string, number> = {};
    results.forEach((r) => {
      sourceTypeCounts[r.source_type] = (sourceTypeCounts[r.source_type] || 0) + 1;
    });
    const topSourceType = Object.entries(sourceTypeCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || null;

    // Calculate score distribution (0-0.2, 0.2-0.4, 0.4-0.6, 0.6-0.8, 0.8-1.0)
    const scoreDistribution = [0, 0, 0, 0, 0];
    results.forEach((r) => {
      const index = Math.min(4, Math.floor(r.final_score * 5));
      scoreDistribution[index]++;
    });

    return {
      averageSimilarity: Math.round(averageSimilarity * 100) / 100,
      averageRecency: Math.round(averageRecency * 100) / 100,
      averageUsage: Math.round(averageUsage * 100) / 100,
      averageFinalScore: Math.round(averageFinalScore * 100) / 100,
      topSourceType,
      scoreDistribution,
    };
  }, [results]);

  return analytics;
}

/**
 * Hook for debounced search state
 * @param initialQuery Initial search query
 * @param debounceMs Debounce delay
 */
export function useDebouncedSearchQuery(initialQuery: string = '', debounceMs: number = 300) {
  const [inputValue, setInputValue] = useState(initialQuery);
  const [debouncedValue, setDebouncedValue] = useState(initialQuery);
  const debounceTimer = useRef<NodeJS.Timeout | undefined>(undefined);

  useEffect(() => {
    debounceTimer.current = setTimeout(() => {
      setDebouncedValue(inputValue);
    }, debounceMs);

    return () => {
      if (debounceTimer.current) {
        clearTimeout(debounceTimer.current);
      }
    };
  }, [inputValue, debounceMs]);

  return {
    inputValue,
    setInputValue,
    debouncedValue,
  };
}

/**
 * Hook that combines all search functionality
 * @param apiUrl Backend API URL
 * @param workspaceId Current workspace UUID
 */
export function useFullSemanticSearch(apiUrl: string, workspaceId: string) {
  const searchObj = useSemanticSearch(apiUrl, workspaceId);
  const filtersObj = useSearchFilters();
  const historyObj = useSearchHistory();
  const trendingObj = useTrendingSearches(apiUrl, workspaceId, 5, true);
  const { logClick } = useSearchClick(apiUrl, workspaceId);
  const analyticsObj = useSearchAnalytics(searchObj.results);
  const { inputValue, setInputValue, debouncedValue } = useDebouncedSearchQuery();

  // Trigger search when debounced value changes
  useEffect(() => {
    if (debouncedValue) {
      searchObj.search(debouncedValue, filtersObj.filters);
      historyObj.addSearch(debouncedValue);
    }
  }, [debouncedValue, filtersObj.filters]);

  return {
    // Search functionality
    query: searchObj.query,
    results: searchObj.results,
    loading: searchObj.loading,
    error: searchObj.error,
    took_ms: searchObj.took_ms,
    cached: searchObj.cached,
    resultCount: searchObj.resultCount,
    
    // Input management
    inputValue,
    setInputValue,
    
    // Filters
    filters: filtersObj.filters,
    updateFilter: filtersObj.updateFilter,
    clearFilters: filtersObj.clearFilters,
    hasActiveFilters: filtersObj.hasActiveFilters,
    
    // History
    history: historyObj.history,
    addSearch: historyObj.addSearch,
    removeSearch: historyObj.removeSearch,
    clearHistory: historyObj.clearHistory,
    
    // Trending
    trending: trendingObj.trending,
    trendingLoading: trendingObj.loading,
    refreshTrending: trendingObj.refresh,
    
    // Analytics
    analytics: analyticsObj,
    
    // Actions
    logClick,
    clearSearch: searchObj.clearSearch,
  };
}
