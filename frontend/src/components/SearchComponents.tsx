/**
 * Production-ready semantic search components for React
 */

import React, { useCallback, useMemo } from 'react';
import type { SemanticSearchResult } from '../lib/search';
import { scoreToReadable, extractSnippet } from '../lib/search';
import { useFullSemanticSearch } from '../hooks/useSearch';

// Type definitions
interface SearchComponentProps {
  apiUrl: string;
  workspaceId: string;
}

interface SearchResultCardProps {
  result: SemanticSearchResult;
  onResultClick?: (result: SemanticSearchResult) => void;
}

interface SearchFiltersProps {
  onTagsChange?: (tags: string[]) => void;
  onSourceTypeChange?: (sourceType: string) => void;
  onDateFromChange?: (date: string) => void;
  onDateToChange?: (date: string) => void;
}

/**
 * Semantic search bar component
 */
export function SemanticSearchBar({
  apiUrl,
  workspaceId,
}: SearchComponentProps) {
  const {
    inputValue,
    setInputValue,
    loading,
  } = useFullSemanticSearch(apiUrl, workspaceId);

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setInputValue(e.target.value);
    },
    [setInputValue]
  );

  const handleClear = useCallback(() => {
    setInputValue('');
  }, [setInputValue]);

  return (
    <div className="w-full max-w-2xl mx-auto">
      <div className="relative">
        <input
          type="text"
          value={inputValue}
          onChange={handleInputChange}
          placeholder="Search documents semantically..."
          className="w-full px-4 py-3 pr-12 rounded-lg border border-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
        {loading && (
          <div className="absolute right-4 top-1/2 transform -translate-y-1/2">
            <div className="animate-spin">
              <svg className="w-5 h-5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4" />
              </svg>
            </div>
          </div>
        )}
        {inputValue && !loading && (
          <button
            onClick={handleClear}
            className="absolute right-4 top-1/2 transform -translate-y-1/2 text-gray-400 hover:text-gray-600"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Individual search result card
 */
export function SearchResultCard({ result, onResultClick }: SearchResultCardProps) {
  const scoreInfo = scoreToReadable(
    result.final_score,
    result.similarity_score,
    result.recency_score,
    result.usage_score
  );

  const handleClick = useCallback(() => {
    onResultClick?.(result);
  }, [result, onResultClick]);

  return (
    <div
      onClick={handleClick}
      className="p-4 border border-gray-200 rounded-lg hover:shadow-md transition-shadow cursor-pointer bg-white"
    >
      {/* Document title and metadata */}
      <div className="flex items-start justify-between mb-2">
        <div>
          <h3 className="font-semibold text-gray-900 text-lg">
            {result.document_title}
          </h3>
          <p className="text-sm text-gray-500">
            Chunk {result.chunk_index} • {result.source_type}
          </p>
        </div>
        <div className="text-right">
          <div
            className="px-3 py-1 rounded-full text-white text-sm font-medium"
            style={{ backgroundColor: scoreInfo.color }}
          >
            {(result.final_score * 100).toFixed(0)}%
          </div>
          <p className="text-xs text-gray-500 mt-1">{scoreInfo.label}</p>
        </div>
      </div>

      {/* Highlight snippet */}
      <div className="mb-3 text-gray-700">
        <p className="text-sm leading-relaxed italic border-l-2 border-blue-300 pl-3">
          "{result.highlight}"
        </p>
      </div>

      {/* Score breakdown */}
      <div className="grid grid-cols-3 gap-2 mb-3">
        <div className="text-center p-2 bg-gray-50 rounded">
          <p className="text-xs text-gray-600">Similarity</p>
          <p className="text-sm font-semibold text-gray-900">
            {(result.similarity_score * 100).toFixed(0)}%
          </p>
        </div>
        <div className="text-center p-2 bg-gray-50 rounded">
          <p className="text-xs text-gray-600">Recency</p>
          <p className="text-sm font-semibold text-gray-900">
            {(result.recency_score * 100).toFixed(0)}%
          </p>
        </div>
        <div className="text-center p-2 bg-gray-50 rounded">
          <p className="text-xs text-gray-600">Usage</p>
          <p className="text-sm font-semibold text-gray-900">
            {(result.usage_score * 100).toFixed(0)}%
          </p>
        </div>
      </div>

      {/* Metadata */}
      <div className="flex items-center justify-between text-xs text-gray-500">
        <span>{result.interaction_count} interactions</span>
        <span>{new Date(result.created_at).toLocaleDateString()}</span>
      </div>
    </div>
  );
}

/**
 * Search results container
 */
export function SearchResults({
  apiUrl,
  workspaceId,
}: SearchComponentProps) {
  const {
    results,
    loading,
    error,
    took_ms,
    cached,
    resultCount,
  } = useFullSemanticSearch(apiUrl, workspaceId);

  if (loading && results.length === 0) {
    return (
      <div className="flex items-center justify-center py-8">
        <div className="text-center">
          <div className="animate-spin mb-4">
            <svg className="w-12 h-12 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4" />
            </svg>
          </div>
          <p className="text-gray-600">Searching...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 bg-red-50 border border-red-200 rounded-lg">
        <p className="text-red-800 font-medium">Search Error</p>
        <p className="text-red-600 text-sm">{error}</p>
      </div>
    );
  }

  if (results.length === 0) {
    return (
      <div className="text-center py-8">
        <p className="text-gray-600">No results found. Try different search terms.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Results header */}
      <div className="flex items-center justify-between mb-4">
        <p className="text-sm text-gray-600">
          Found <span className="font-semibold">{resultCount}</span> results
          {cached && <span className="ml-2 text-xs bg-blue-100 text-blue-800 px-2 py-1 rounded">cached</span>}
          {took_ms > 0 && <span className="ml-2 text-xs text-gray-500">({took_ms}ms)</span>}
        </p>
      </div>

      {/* Results list */}
      <div className="space-y-3">
        {results.map((result) => (
          <SearchResultCard
            key={result.chunk_id}
            result={result}
            onResultClick={(r) => {
              console.log('Clicked result:', r);
            }}
          />
        ))}
      </div>
    </div>
  );
}

/**
 * Trending searches widget
 */
export function TrendingSearchesWidget({
  apiUrl,
  workspaceId,
}: SearchComponentProps) {
  const {
    trending,
    trendingLoading,
  } = useFullSemanticSearch(apiUrl, workspaceId);

  if (trendingLoading) {
    return (
      <div className="p-4 bg-gray-50 rounded-lg text-center">
        <p className="text-sm text-gray-600">Loading trends...</p>
      </div>
    );
  }

  if (!trending || trending.length === 0) {
    return null;
  }

  return (
    <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
      <h3 className="font-semibold text-blue-900 mb-3">Trending Searches</h3>
      <div className="space-y-2">
        {trending.slice(0, 5).map((item, idx) => (
          <div
            key={idx}
            className="flex items-center justify-between text-sm bg-white p-2 rounded hover:bg-blue-100 cursor-pointer transition-colors"
          >
            <span className="text-gray-800">{item.query}</span>
            <span className="text-gray-500 text-xs">
              {item.search_count} searches • {item.unique_users} users
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Search filters component
 */
export function SearchFilters({ onSourceTypeChange }: SearchFiltersProps) {
  const sourceTypes = ['upload', 'slack', 'notion', 'gdrive', 'github', 'email', 'web_clip'];

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <h3 className="font-semibold text-gray-900 mb-4">Filters</h3>

      <div className="space-y-4">
        {/* Source type filter */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Source Type
          </label>
          <div className="grid grid-cols-2 gap-2">
            {sourceTypes.map((type) => (
              <label key={type} className="flex items-center">
                <input
                  type="checkbox"
                  value={type}
                  onChange={(e) => onSourceTypeChange?.(e.target.value)}
                  className="rounded border-gray-300"
                />
                <span className="ml-2 text-sm text-gray-700 capitalize">{type}</span>
              </label>
            ))}
          </div>
        </div>

        {/* Date range filter */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Date Range
          </label>
          <div className="grid grid-cols-2 gap-2">
            <input
              type="date"
              placeholder="From"
              className="px-3 py-2 border border-gray-300 rounded text-sm"
            />
            <input
              type="date"
              placeholder="To"
              className="px-3 py-2 border border-gray-300 rounded text-sm"
            />
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Complete semantic search interface
 */
export function SemanticSearchInterface({
  apiUrl,
  workspaceId,
}: SearchComponentProps) {
  return (
    <div className="w-full min-h-screen bg-gradient-to-b from-blue-50 to-white">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 py-6 px-4">
        <div className="max-w-6xl mx-auto">
          <h1 className="text-3xl font-bold text-gray-900 mb-2">
            Semantic Search
          </h1>
          <p className="text-gray-600">
            Find documents using AI-powered semantic search with intelligent ranking
          </p>
        </div>
      </div>

      {/* Main content */}
      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-8">
          {/* Search bar and results */}
          <div className="lg:col-span-3 space-y-6">
            <SemanticSearchBar apiUrl={apiUrl} workspaceId={workspaceId} />
            <SearchResults apiUrl={apiUrl} workspaceId={workspaceId} />
          </div>

          {/* Sidebar */}
          <div className="space-y-6">
            <TrendingSearchesWidget apiUrl={apiUrl} workspaceId={workspaceId} />
            <SearchFilters />
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Minimal search widget for embedding in pages
 */
export function MiniSearchWidget({
  apiUrl,
  workspaceId,
  onResultSelect,
}: SearchComponentProps & { onResultSelect?: (result: SemanticSearchResult) => void }) {
  const {
    inputValue,
    setInputValue,
    results,
    loading,
  } = useFullSemanticSearch(apiUrl, workspaceId);

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-300 p-4">
      <div className="relative">
        <input
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder="Quick search..."
          className="w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />

        {/* Dropdown results */}
        {inputValue && results.length > 0 && (
          <div className="absolute top-full left-0 right-0 mt-2 bg-white border border-gray-300 rounded shadow-lg z-10 max-h-64 overflow-y-auto">
            {results.slice(0, 5).map((result) => (
              <div
                key={result.chunk_id}
                onClick={() => {
                  onResultSelect?.(result);
                  setInputValue('');
                }}
                className="p-3 hover:bg-gray-50 cursor-pointer border-b border-gray-200 last:border-b-0"
              >
                <p className="font-medium text-sm text-gray-900">{result.document_title}</p>
                <p className="text-xs text-gray-500 truncate">{result.highlight}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
