/**
 * App Router Component
 * Defines all application routes with authentication guards
 */

import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { lazy, Suspense } from 'react';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import { EventsProvider } from '@/contexts/EventsContext';
import { PrivateRoute } from '@/components/PrivateRoute';
import { api } from '@/lib/api';

const App = lazy(() => import('@/App'));
const LoginPage = lazy(() => import('@/pages/LoginPage').then((module) => ({ default: module.LoginPage })));
const RegisterPage = lazy(() => import('@/pages/RegisterPage').then((module) => ({ default: module.RegisterPage })));
const GoogleAuthCallbackPage = lazy(() =>
  import('@/pages/GoogleAuthCallbackPage').then((module) => ({ default: module.GoogleAuthCallbackPage })),
);
const AcceptNoteInvitePage = lazy(() =>
  import('@/pages/AcceptNoteInvitePage').then((module) => ({ default: module.AcceptNoteInvitePage })),
);
const SharedNotePage = lazy(() => import('@/pages/SharedNotePage').then((module) => ({ default: module.SharedNotePage })));

function RouteLoadingFallback() {
  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#0A0A0A',
        color: '#888888',
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 11,
        letterSpacing: '0.1em',
        textTransform: 'uppercase',
      }}
    >
      Loading...
    </div>
  );
}

/**
 * Inner router component that has access to auth context
 */
function InnerRouter() {
  const { user, currentWorkspaceId } = useAuth();
  const token = user ? api.getToken() ?? undefined : undefined;

  return (
    <EventsProvider workspaceId={currentWorkspaceId || undefined} token={token}>
      <Suspense fallback={<RouteLoadingFallback />}>
        <Routes>
          {/* Public Routes */}
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/auth/google/callback" element={<GoogleAuthCallbackPage />} />

          {/* Protected Routes */}
          <Route
            path="/note-invites/accept"
            element={
              <PrivateRoute>
                <AcceptNoteInvitePage />
              </PrivateRoute>
            }
          />
          <Route
            path="/shared-notes/:noteId"
            element={
              <PrivateRoute>
                <SharedNotePage />
              </PrivateRoute>
            }
          />
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
      </Suspense>
    </EventsProvider>
  );
}

/**
 * Main router component
 * Wraps the app with routing and auth provider
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
