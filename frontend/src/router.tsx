/**
 * App Router Component
 * Defines all application routes with authentication guards
 */

import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import { EventsProvider } from '@/contexts/EventsContext';
import { PrivateRoute } from '@/components/PrivateRoute';
import App from '@/App';
import { LoginPage } from '@/pages/LoginPage';
import { RegisterPage } from '@/pages/RegisterPage';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

/**
 * Inner router component that has access to auth context
 */
function InnerRouter() {
  const { user, currentWorkspaceId } = useAuth();
  const [token, setToken] = useState<string | undefined>();

  // Get token from API module
  useEffect(() => {
    const authToken = api.getToken();
    console.debug('🔐 [TOKEN UPDATE] Token fetched from API:', authToken ? '****' : 'undefined');
    console.debug('🔐 [WORKSPACE ID] Current workspace:', currentWorkspaceId);
    if (authToken) {
      setToken(authToken);
      console.log('✅ [TOKEN SET] Token successfully set for WebSocket:', authToken ? '****' : 'undefined');
    } else {
      console.warn('⚠️ [TOKEN MISSING] No token available for WebSocket connection');
      setToken(undefined);
    }
  }, [user, currentWorkspaceId]);

  return (
    <EventsProvider workspaceId={currentWorkspaceId || undefined} token={token}>
      <Routes>
        {/* Public Routes */}
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />

        {/* Protected Routes */}
        <Route
          path="/*"
          element={
            <PrivateRoute>
              <App />
            </PrivateRoute>
          }
        />

        {/* Catch all - redirect to dashboard */}
        <Route path="/" element={<Navigate to="/login" replace />} />
      </Routes>
    </EventsProvider>
  );
}

/**
 * Main router component
 * Wraps the entire app with routing and auth provider
 */
export function AppRouter() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <InnerRouter />
      </AuthProvider>
    </BrowserRouter>
  );
}
