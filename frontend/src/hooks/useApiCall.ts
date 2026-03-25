/**
 * useApi Hook - Error handling and request state management
 * Provides loading states, error handling, and retry logic for API calls
 */

import { useState, useCallback, useRef } from 'react';
import { ApiError, AuthenticationError, ValidationError } from '@/lib/api';

export interface UseApiState<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
  retrying: boolean;
}

export interface UseApiOptions {
  onSuccess?: (data: any) => void;
  onError?: (error: ApiError) => void;
  retryCount?: number;
}

/**
 * Hook for handling API calls with built-in state management
 */
export function useApiCall<T = any>(options: UseApiOptions = {}) {
  const [state, setState] = useState<UseApiState<T>>({
    data: null,
    loading: false,
    error: null,
    retrying: false,
  });

  const retryCountRef = useRef(0);
  const maxRetries = options.retryCount ?? 3;

  const execute = useCallback(
    async (apiCall: () => Promise<T>) => {
      retryCountRef.current = 0;

      const attempt = async (): Promise<T> => {
        try {
          setState((prev) => ({ ...prev, loading: true, error: null }));

          const data = await apiCall();

          setState((prev) => ({
            ...prev,
            data,
            loading: false,
            error: null,
            retrying: false,
          }));

          options.onSuccess?.(data);
          return data;
        } catch (err) {
          const error = err instanceof ApiError ? err : new ApiError(500, 'UNKNOWN', String(err));

          // Auto-clear auth errors (user needs to log in)
          if (error instanceof AuthenticationError) {
            localStorage.removeItem('token');
          }

          // Retry logic for retryable errors
          if (error.status >= 500 && retryCountRef.current < maxRetries) {
            retryCountRef.current += 1;
            setState((prev) => ({ ...prev, retrying: true }));

            // Exponential backoff
            const delay = Math.pow(2, retryCountRef.current - 1) * 1000;
            await new Promise((resolve) => setTimeout(resolve, delay));

            return attempt();
          }

          setState((prev) => ({
            ...prev,
            error,
            loading: false,
            retrying: false,
          }));

          options.onError?.(error);
          throw error;
        }
      };

      return attempt();
    },
    [options, maxRetries]
  );

  const retry = useCallback(() => {
    // Reset for manual retry
    retryCountRef.current = 0;
  }, []);

  return {
    ...state,
    execute,
    retry,
  };
}

/**
 * Hook for managing paginated API calls
 */
export function usePaginatedApi<T = any>(options: UseApiOptions = {}) {
  const [state, setState] = useState<UseApiState<T[]>>({
    data: [],
    loading: false,
    error: null,
    retrying: false,
  });

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [total, setTotal] = useState(0);

  const execute = useCallback(
    async (apiCall: (page: number, pageSize: number) => Promise<any>) => {
      try {
        setState((prev) => ({ ...prev, loading: true, error: null }));

        const result = await apiCall(page, pageSize);

        setState((prev) => ({
          ...prev,
          data: result.items || result,
          loading: false,
          error: null,
        }));

        if (result.total !== undefined) {
          setTotal(result.total);
        }

        options.onSuccess?.(result);
        return result;
      } catch (err) {
        const error = err instanceof ApiError ? err : new ApiError(500, 'UNKNOWN', String(err));

        setState((prev) => ({
          ...prev,
          error,
          loading: false,
        }));

        options.onError?.(error);
        throw error;
      }
    },
    [page, pageSize, options]
  );

  return {
    ...state,
    execute,
    page,
    setPage,
    pageSize,
    setPageSize,
    total,
    pageCount: Math.ceil(total / pageSize),
  };
}

/**
 * Hook for form submission with API integration
 */
export function useApiForm<T = any>(
  onSubmit: (data: T) => Promise<any>,
  options: UseApiOptions = {}
) {
  const [state, setState] = useState({
    loading: false,
    error: null as ApiError | null,
    success: false,
  });

  const handleSubmit = useCallback(
    async (data: T) => {
      try {
        setState({ loading: true, error: null, success: false });

        const result = await onSubmit(data);

        setState({ loading: false, error: null, success: true });

        options.onSuccess?.(result);
        return result;
      } catch (err) {
        const error = err instanceof ApiError ? err : new ApiError(500, 'UNKNOWN', String(err));

        setState({ loading: false, error, success: false });

        options.onError?.(error);
        throw error;
      }
    },
    [onSubmit, options]
  );

  const reset = useCallback(() => {
    setState({ loading: false, error: null, success: false });
  }, []);

  return { ...state, handleSubmit, reset };
}
