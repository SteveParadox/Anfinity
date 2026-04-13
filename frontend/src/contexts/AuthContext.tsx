/**
 * Auth Context for managing authentication state
 * Provides user info, tokens, workspaces, and auth methods globally
 */

import { createContext, useContext, useState, useEffect, useRef, useCallback, type ReactNode } from 'react';
import { api } from '@/lib/api';

export type WorkspaceRole = 'owner' | 'admin' | 'member' | 'viewer';

export interface Workspace {
  id: string;
  name: string;
  role: WorkspaceRole;
}

export interface User {
  id: string;
  email: string;
  name?: string;
  full_name?: string;
  plan?: 'free' | 'pro' | 'team' | 'enterprise';
  is_active?: boolean;
  created_at?: string;
}

export interface AuthContextType {
  user: User | null;
  workspaces: Workspace[];
  currentWorkspaceId: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  error: string | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName?: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshAuth: () => Promise<void>;
  refreshWorkspaces: () => Promise<void>;
  setCurrentWorkspace: (workspaceId: string) => void;
  clearError: () => void;
  hasRole: (workspaceId: string, minRole: WorkspaceRole) => boolean;
}

export const AuthContext = createContext<AuthContextType | undefined>(undefined);

export interface AuthProviderProps {
  children: ReactNode;
}

type TokenResponse = Awaited<ReturnType<typeof api.login>>;

const ROLE_HIERARCHY: Record<WorkspaceRole, number> = {
  owner: 4,
  admin: 3,
  member: 2,
  viewer: 1,
};

export function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<User | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [currentWorkspaceId, setCurrentWorkspaceId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const tokenRefreshIntervalRef = useRef<NodeJS.Timeout | null>(null);

  const clearWorkspaceState = useCallback(() => {
    setWorkspaces([]);
    setCurrentWorkspaceId(null);
    localStorage.removeItem('currentWorkspaceId');
  }, []);

  const normalizeWorkspaces = useCallback((items: Array<any> = []): Workspace[] => {
    return items
      .filter((workspace) => workspace && workspace.id && workspace.name)
      .map((workspace) => ({
        id: String(workspace.id),
        name: String(workspace.name),
        role: (workspace.role || 'member') as WorkspaceRole,
      }));
  }, []);

  const reconcileWorkspaceSelection = useCallback((
    nextWorkspaces: Workspace[],
    preferredWorkspaceId?: string | null,
  ): string | null => {
    const validWorkspaceIds = new Set(nextWorkspaces.map((workspace) => workspace.id));
    const savedWorkspaceId = localStorage.getItem('currentWorkspaceId');
    const candidates = [
      preferredWorkspaceId,
      currentWorkspaceId,
      savedWorkspaceId,
      nextWorkspaces[0]?.id ?? null,
    ];

    const resolvedWorkspaceId = candidates.find((workspaceId): workspaceId is string => {
      return typeof workspaceId === 'string' && validWorkspaceIds.has(workspaceId);
    }) || null;

    setWorkspaces(nextWorkspaces);
    setCurrentWorkspaceId(resolvedWorkspaceId);

    if (resolvedWorkspaceId) {
      localStorage.setItem('currentWorkspaceId', resolvedWorkspaceId);
    } else {
      localStorage.removeItem('currentWorkspaceId');
    }

    return resolvedWorkspaceId;
  }, [currentWorkspaceId]);

  const applyAuthPayload = useCallback(async (
    response: TokenResponse,
    options?: { preferredWorkspaceId?: string | null; fallbackToList?: boolean },
  ) => {
    api.setToken(response.access_token);

    setUser({
      id: response.user.id,
      email: response.user.email,
      full_name: response.user.full_name,
      is_active: true,
      created_at: new Date().toISOString(),
    });

    const nextWorkspaces = normalizeWorkspaces(response.workspaces || []);
    if (nextWorkspaces.length > 0) {
      reconcileWorkspaceSelection(nextWorkspaces, options?.preferredWorkspaceId);
      return;
    }

    if (options?.fallbackToList) {
      const listedWorkspaces = normalizeWorkspaces(await api.listWorkspaces());
      reconcileWorkspaceSelection(listedWorkspaces, options?.preferredWorkspaceId);
      return;
    }

    clearWorkspaceState();
  }, [clearWorkspaceState, normalizeWorkspaces, reconcileWorkspaceSelection]);

  const loadWorkspaces = useCallback(async () => {
    try {
      const response = await api.listWorkspaces();
      const nextWorkspaces = normalizeWorkspaces(response);
      reconcileWorkspaceSelection(nextWorkspaces);
    } catch (err) {
      console.error('Failed to load workspaces:', err);
    }
  }, [normalizeWorkspaces, reconcileWorkspaceSelection]);

  const setupTokenRefresh = useCallback(() => {
    if (tokenRefreshIntervalRef.current) {
      clearInterval(tokenRefreshIntervalRef.current);
    }

    tokenRefreshIntervalRef.current = setInterval(async () => {
      try {
        if (!api.isAuthenticated()) {
          return;
        }

        const response = await api.refresh();
        await applyAuthPayload(response, {
          preferredWorkspaceId: currentWorkspaceId,
          fallbackToList: true,
        });
      } catch (err) {
        console.error('Token refresh failed:', err);
        api.clearToken();
        setUser(null);
        clearWorkspaceState();
      }
    }, 12 * 60 * 60 * 1000);
  }, [applyAuthPayload, clearWorkspaceState, currentWorkspaceId]);

  useEffect(() => {
    const initializeAuth = async () => {
      try {
        if (!api.isAuthenticated()) {
          return;
        }

        const response = await api.refresh();
        await applyAuthPayload(response, { fallbackToList: true });
        setupTokenRefresh();
      } catch (refreshErr) {
        console.error('Failed to refresh token on init:', refreshErr);
        api.clearToken();
        setUser(null);
        clearWorkspaceState();
      } finally {
        setIsLoading(false);
      }
    };

    void initializeAuth();

    return () => {
      if (tokenRefreshIntervalRef.current) {
        clearInterval(tokenRefreshIntervalRef.current);
      }
    };
  }, [applyAuthPayload, clearWorkspaceState, setupTokenRefresh]);

  const login = async (email: string, password: string) => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await api.login({ email, password });
      await applyAuthPayload(response);
      setupTokenRefresh();
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Login failed';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  };

  const register = async (email: string, password: string, fullName?: string) => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await api.register({
        email,
        password,
        full_name: fullName,
      });
      await applyAuthPayload(response);
      setupTokenRefresh();
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Registration failed';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  };

  const logout = async () => {
    setIsLoading(true);
    setError(null);

    try {
      if (tokenRefreshIntervalRef.current) {
        clearInterval(tokenRefreshIntervalRef.current);
        tokenRefreshIntervalRef.current = null;
      }
      await api.logout();
      api.clearToken();
      setUser(null);
      clearWorkspaceState();
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Logout failed';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  };

  const setCurrentWorkspace = useCallback((workspaceId: string) => {
    if (!workspaces.some((workspace) => workspace.id === workspaceId)) {
      console.warn('Ignoring unknown workspace selection:', workspaceId);
      return;
    }

    setCurrentWorkspaceId(workspaceId);
    localStorage.setItem('currentWorkspaceId', workspaceId);
  }, [workspaces]);

  const hasRole = useCallback((workspaceId: string, minRole: WorkspaceRole): boolean => {
    const workspace = workspaces.find((item) => item.id === workspaceId);
    if (!workspace) {
      return false;
    }
    return ROLE_HIERARCHY[workspace.role] >= ROLE_HIERARCHY[minRole];
  }, [workspaces]);

  const refreshAuth = async () => {
    try {
      const response = await api.refresh();
      await applyAuthPayload(response, {
        preferredWorkspaceId: currentWorkspaceId,
        fallbackToList: true,
      });
    } catch (err) {
      api.clearToken();
      setUser(null);
      clearWorkspaceState();
      throw err;
    }
  };

  const clearError = () => setError(null);

  const value: AuthContextType = {
    user,
    workspaces,
    currentWorkspaceId,
    isLoading,
    isAuthenticated: user !== null,
    error,
    login,
    register,
    logout,
    refreshAuth,
    refreshWorkspaces: loadWorkspaces,
    setCurrentWorkspace,
    clearError,
    hasRole,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }

  return context;
}
