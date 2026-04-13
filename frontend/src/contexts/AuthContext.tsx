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
  // State
  user: User | null;
  workspaces: Workspace[];
  currentWorkspaceId: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  error: string | null;
  
  // Methods
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

/**
 * Auth Provider Component
 * Wraps the application and provides auth context
 */
export function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<User | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [currentWorkspaceId, setCurrentWorkspaceId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const tokenRefreshIntervalRef = useRef<NodeJS.Timeout | null>(null);

  /**
   * Load user's workspaces
   */
  const loadWorkspaces = useCallback(async () => {
    console.log('📋 [LOAD WORKSPACES START] Fetching user workspaces');
    try {
      console.debug('🔄 [API CALL] Calling api.listWorkspaces()');
      const response = await api.listWorkspaces(); 
      console.debug('✅ [API RESPONSE] Received %d workspaces', response.length);
      
      const formattedWorkspaces: Workspace[] = response.map((w: any) => ({
        id: w.id,
        name: w.name,
        role: w.role || 'member',
      }));

      console.log('📝 [FORMAT] Formatted workspaces - Count:', formattedWorkspaces.length);
      setWorkspaces(formattedWorkspaces);

      if (formattedWorkspaces.length > 0) {
        console.debug('🔍 [CURRENT WORKSPACE] Determining current workspace');
        const savedWorkspaceId = localStorage.getItem('currentWorkspaceId');
        const workspaceId =
          savedWorkspaceId && formattedWorkspaces.some(w => w.id === savedWorkspaceId)
            ? savedWorkspaceId
            : formattedWorkspaces[0].id;

        console.log('✅ [CURRENT WORKSPACE SET] Workspace ID:', workspaceId);
        setCurrentWorkspaceId(workspaceId);
        localStorage.setItem('currentWorkspaceId', workspaceId);
      }
      console.log('✅ [LOAD WORKSPACES SUCCESS] Workspaces loaded');
    } catch (err) {
      console.error('❌ [LOAD WORKSPACES FAILED] Failed to load workspaces:', err);
    }
  }, []);

  /**
   * Setup automatic token refresh
   */
  const setupTokenRefresh = useCallback(() => {
    console.log('⏰ [TOKEN REFRESH SETUP] Initializing token refresh mechanism');
    
    // Clear existing interval
    if (tokenRefreshIntervalRef.current) {
      console.debug('🧹 [CLEANUP] Clearing existing token refresh interval');
      clearInterval(tokenRefreshIntervalRef.current);
    }

    // Refresh token every 12 hours (token expires in 24 hours)
    // This ensures token stays fresh without frequent refreshes
    console.debug('📋 [SCHEDULE] Scheduling token refresh every 12 hours');
    tokenRefreshIntervalRef.current = setInterval(async () => {
      try {
        console.debug('🔄 [TOKEN REFRESH] Attempting to refresh token');
        if (api.isAuthenticated()) {
          console.debug('✅ [AUTHENTICATED] User is authenticated, proceeding with refresh');
          await api.refresh();
          console.log('✅ [TOKEN REFRESHED] Token successfully refreshed');
        } else {
          console.debug('⚠️ [NOT AUTHENTICATED] User not authenticated, skipping refresh');
        }
      } catch (err) {
        console.error('❌ [TOKEN REFRESH FAILED] Token refresh failed:', err);
        // If refresh fails, logout the user
        console.log('🔐 [LOGOUT] Clearing user session due to token refresh failure');
        api.clearToken();
        setUser(null);
        setWorkspaces([]);
        setCurrentWorkspaceId(null);
      }
    }, 12 * 60 * 60 * 1000); // 12 hours
    console.log('✅ [TOKEN REFRESH SETUP SUCCESS] Token refresh scheduled');
  }, []);

  /**
   * Initialize auth state on mount
   * Check if user already has a valid token
   */
  useEffect(() => {
    const initializeAuth = async () => {
      console.log('🔐 [AUTH INIT START] Initializing authentication state');
      try {
        console.debug('🔍 [CHECK TOKEN] Checking if user has existing token');
        if (api.isAuthenticated()) {
          console.log('✅ [TOKEN EXISTS] Valid token found, attempting refresh');
          try {
            console.debug('🔄 [REFRESH] Calling api.refresh()');
            const response = await api.refresh();
            console.debug('✅ [TOKEN UPDATED] Setting new token from refresh');
            api.setToken(response.access_token);

            console.log('👤 [USER SET] Setting user state - Email:', response.user.email);
            setUser({
              id: response.user.id,
              email: response.user.email,
              full_name: response.user.full_name,
              is_active: true,
              created_at: new Date().toISOString(),
            });

            console.debug('📦 [LOAD WORKSPACES] Loading workspaces for user');
            await loadWorkspaces();
            console.debug('⏰ [SETUP REFRESH] Setting up token refresh timer');
            setupTokenRefresh();
            console.log('✅ [AUTH INIT SUCCESS] Authentication initialized successfully');
          } catch (refreshErr) {
            // If refresh fails, clear token and treat as logged out
            console.error('❌ [REFRESH FAILED] Failed to refresh token on init:', refreshErr);
            console.log('🔐 [LOGOUT] Clearing authentication due to refresh failure');
            api.clearToken();
            setUser(null);
            setWorkspaces([]);
            setCurrentWorkspaceId(null);
          }
        } else {
          console.log('ℹ️ [NO TOKEN] No token found, user is not authenticated');
        }
      } catch (err) {
        console.error('❌ [AUTH INIT ERROR] Auth initialization error:', err);
      } finally {
        console.debug('✅ [LOADING COMPLETE] Marking initialization as complete');
        setIsLoading(false);
      }
    };

    initializeAuth();

    // Cleanup on unmount
    return () => {
      if (tokenRefreshIntervalRef.current) {
        console.debug('🧹 [CLEANUP] Clearing token refresh interval on unmount');
        clearInterval(tokenRefreshIntervalRef.current);
      }
    };
  }, [loadWorkspaces, setupTokenRefresh]);

  /**
   * Handle login
   */
  const login = async (email: string, password: string) => {
    console.log('🔐 [LOGIN START] Attempting login - Email:', email);
    setIsLoading(true);
    setError(null);

    try {
      console.debug('📡 [API CALL] Calling api.login()');
      const response = await api.login({ email, password });
      console.debug('✅ [API RESPONSE] Login successful');
      
      // Store token
      console.debug('🔑 [TOKEN] Setting authentication token');
      api.setToken(response.access_token);
      
      // Set user
      console.log('👤 [USER] Setting user state - Email:', response.user.email);
      setUser({
        id: response.user.id,
        email: response.user.email,
        full_name: response.user.full_name,
        is_active: true,
        created_at: new Date().toISOString(),
      });

      // Set workspaces
      console.debug('📋 [WORKSPACES] Processing workspaces from response');
      if (response.workspaces && Array.isArray(response.workspaces)) {
        console.log('📦 [WORKSPACES SET] User has %d workspaces', response.workspaces.length);
        setWorkspaces(response.workspaces);
        if (response.workspaces.length > 0) {
          const defaultId = response.workspaces[0].id;
          console.log('✅ [DEFAULT WORKSPACE] Set to:', defaultId);
          setCurrentWorkspaceId(defaultId);
          localStorage.setItem('currentWorkspaceId', defaultId);
        }
      }
      console.log('✅ [LOGIN SUCCESS] User logged in successfully');
    } catch (err) {
      const errorMessage = 
        err instanceof Error ? err.message : 'Login failed';
      console.error('❌ [LOGIN FAILED] Login error:', errorMessage);
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  };

  /**
   * Handle registration
   */
  const register = async (
    email: string,
    password: string,
    fullName?: string
  ) => {
    console.log('📝 [REGISTER START] Attempting registration - Email:', email);
    setIsLoading(true);
    setError(null);

    try {
      console.debug('📡 [API CALL] Calling api.register()');
      const response = await api.register({
        email,
        password,
        full_name: fullName,
      });
      console.debug('✅ [API RESPONSE] Registration successful');

      // Store token
      console.debug('🔑 [TOKEN] Setting authentication token');
      api.setToken(response.access_token);

      // Set user
      console.log('👤 [USER] Setting user state - Email:', response.user.email);
      setUser({
        id: response.user.id,
        email: response.user.email,
        full_name: response.user.full_name,
        is_active: true,
        created_at: new Date().toISOString(),
      });

      // Set workspaces
      console.debug('📋 [WORKSPACES] Processing workspaces from response');
      if (response.workspaces && Array.isArray(response.workspaces)) {
        console.log('📦 [WORKSPACES SET] User has %d workspaces', response.workspaces.length);
        setWorkspaces(response.workspaces);
        if (response.workspaces.length > 0) {
          const defaultId = response.workspaces[0].id;
          console.log('✅ [DEFAULT WORKSPACE] Set to:', defaultId);
          setCurrentWorkspaceId(defaultId);
          localStorage.setItem('currentWorkspaceId', defaultId);
        }
      }
      console.log('✅ [REGISTER SUCCESS] User registered successfully');
    } catch (err) {
      const errorMessage =
        err instanceof Error ? err.message : 'Registration failed';
      console.error('❌ [REGISTER FAILED] Registration error:', errorMessage);
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  };

  /**
   * Handle logout
   */
  const logout = async () => {
    console.log('🔐 [LOGOUT START] Attempting logout');
    setIsLoading(true);
    setError(null);

    try {
      console.debug('📡 [API CALL] Calling api.logout()');
      await api.logout();
      console.debug('✅ [API RESPONSE] Logout successful');
      
      console.debug('🧹 [CLEANUP] Clearing authentication data');
      api.clearToken();
      setUser(null);
      setWorkspaces([]);
      setCurrentWorkspaceId(null);
      localStorage.removeItem('currentWorkspaceId');
      console.log('✅ [LOGOUT SUCCESS] User logged out successfully');
    } catch (err) {
      const errorMessage =
        err instanceof Error ? err.message : 'Logout failed';
      console.error('❌ [LOGOUT FAILED] Logout error:', errorMessage);
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  };

  /**
   * Set current workspace
   */
  const setCurrentWorkspace = (workspaceId: string) => {
    setCurrentWorkspaceId(workspaceId);
    localStorage.setItem('currentWorkspaceId', workspaceId);
  };

  /**
   * Check if current user has required role in workspace
   */
  const hasRole = (workspaceId: string, minRole: WorkspaceRole): boolean => {
    const workspace = workspaces.find(w => w.id === workspaceId);
    if (!workspace) return false;

    const roleHierarchy: Record<WorkspaceRole, number> = {
      owner: 4,
      admin: 3,
      member: 2,
      viewer: 1,
    };

    return roleHierarchy[workspace.role] >= roleHierarchy[minRole];
  };

  /**
   * Refresh authentication
   */
  const refreshAuth = async () => {
    try {
      const response = await api.refresh();
      api.setToken(response.access_token);
      setUser({
        id: response.user.id,
        email: response.user.email,
        full_name: response.user.full_name,
        is_active: true,
        created_at: new Date().toISOString(),
      });
    } catch (err) {
      api.clearToken();
      setUser(null);
      throw err;
    }
  };

  /**
   * Clear error message
   */
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

/**
 * Hook to use auth context
 */
export function useAuth() {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }

  return context;
}
