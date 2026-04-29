import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../lib/api';
import type {
  DeepPartial,
  ProductUserSettings,
  ProductWorkspaceSettings,
  UserSettingsResponse,
  WorkspaceSettingsResponse,
} from '../lib/api';

type SettingsState = {
  user: UserSettingsResponse | null;
  workspace: WorkspaceSettingsResponse | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  savedAt: number | null;
};

function settingsError(error: unknown): string {
  if (error instanceof Error) {
    // Include HTTP status and details if available
    if ((error as any).status) {
      const status = (error as any).status;
      const code = (error as any).code || 'UNKNOWN_ERROR';
      return `${code} (${status}): ${error.message}`;
    }
    return error.message;
  }
  return 'Settings request failed. Please try again.';
}

// Exponential backoff retry logic for transient failures
async function withRetry<T>(
  fn: () => Promise<T>,
  maxRetries: number = 3,
  initialDelayMs: number = 1000
): Promise<T> {
  let lastError: any;
  
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;
      
      // Don't retry on client errors (4xx) except 429 (rate limit) and 503 (service unavailable)
      const status = (error as any).status;
      if (status && status >= 400 && status < 500 && status !== 429 && status !== 503) {
        throw error;
      }
      
      // Don't retry on last attempt
      if (attempt === maxRetries - 1) {
        break;
      }
      
      // Exponential backoff: 1s, 2s, 4s
      const delayMs = initialDelayMs * Math.pow(2, attempt);
      await new Promise(resolve => setTimeout(resolve, delayMs));
    }
  }
  
  throw lastError;
}

export function useProductSettings(workspaceId?: string | null, enabled = true) {
  const [state, setState] = useState<SettingsState>({
    user: null,
    workspace: null,
    loading: true,
    saving: false,
    error: null,
    savedAt: null,
  });

  const load = useCallback(async () => {
    if (!enabled) {
      setState((prev) => ({ ...prev, loading: false, error: null }));
      return;
    }
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      // Use Promise.all with timeout for concurrent requests
      const timeoutPromise = new Promise((_, reject) =>
        setTimeout(() => reject(new Error('Settings load timeout after 30s')), 30000)
      );
      
      const [user, workspace] = await Promise.race([
        Promise.all([
          withRetry(() => api.getMySettings()),
          workspaceId ? withRetry(() => api.getWorkspaceSettings(workspaceId)).catch(() => null) : Promise.resolve(null),
        ]),
        timeoutPromise,
      ]) as [UserSettingsResponse, WorkspaceSettingsResponse | null];
      
      setState((prev) => ({ ...prev, user, workspace, loading: false, error: null }));
    } catch (error) {
      setState((prev) => ({ ...prev, loading: false, error: settingsError(error) }));
    }
  }, [enabled, workspaceId]);

  useEffect(() => {
    void load();
  }, [load]);

  const updateUserSettings = useCallback(async (patch: DeepPartial<ProductUserSettings>) => {
    setState((prev) => ({ ...prev, saving: true, error: null }));
    try {
      const user = await withRetry(() => api.updateMySettings(patch));
      setState((prev) => ({ ...prev, user, saving: false, savedAt: Date.now() }));
      return user;
    } catch (error) {
      setState((prev) => ({ ...prev, saving: false, error: settingsError(error) }));
      throw error;
    }
  }, []);

  const updateWorkspaceSettings = useCallback(async (patch: DeepPartial<ProductWorkspaceSettings>) => {
    if (!workspaceId) {
      throw new Error('No workspace selected');
    }
    setState((prev) => ({ ...prev, saving: true, error: null }));
    try {
      const workspace = await withRetry(() => api.updateWorkspaceSettings(workspaceId, patch));
      setState((prev) => ({ ...prev, workspace, saving: false, savedAt: Date.now() }));
      return workspace;
    } catch (error) {
      setState((prev) => ({ ...prev, saving: false, error: settingsError(error) }));
      throw error;
    }
  }, [workspaceId]);

  const resetUserSettings = useCallback(async () => {
    setState((prev) => ({ ...prev, saving: true, error: null }));
    try {
      const user = await withRetry(() => api.resetMySettings());
      setState((prev) => ({ ...prev, user, saving: false, savedAt: Date.now() }));
      return user;
    } catch (error) {
      setState((prev) => ({ ...prev, saving: false, error: settingsError(error) }));
      throw error;
    }
  }, []);

  const resetWorkspaceSettings = useCallback(async () => {
    if (!workspaceId) {
      throw new Error('No workspace selected');
    }
    setState((prev) => ({ ...prev, saving: true, error: null }));
    try {
      const workspace = await withRetry(() => api.resetWorkspaceSettings(workspaceId));
      setState((prev) => ({ ...prev, workspace, saving: false, savedAt: Date.now() }));
      return workspace;
    } catch (error) {
      setState((prev) => ({ ...prev, saving: false, error: settingsError(error) }));
      throw error;
    }
  }, [workspaceId]);

  return useMemo(() => ({
    ...state,
    reload: load,
    updateUserSettings,
    updateWorkspaceSettings,
    resetUserSettings,
    resetWorkspaceSettings,
  }), [
    state,
    load,
    updateUserSettings,
    updateWorkspaceSettings,
    resetUserSettings,
    resetWorkspaceSettings,
  ]);
}
