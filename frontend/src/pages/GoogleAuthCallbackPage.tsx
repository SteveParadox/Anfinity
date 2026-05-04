import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AlertCircle, Loader2 } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { api } from '@/lib/api';

function getFragmentParams(): URLSearchParams {
  const fragment = window.location.hash.startsWith('#')
    ? window.location.hash.slice(1)
    : window.location.hash;
  return new URLSearchParams(fragment);
}

function sanitizeRedirectPath(value: string | null): string {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.includes('\\')) {
    return '/dashboard';
  }
  return value;
}

export function GoogleAuthCallbackPage() {
  const navigate = useNavigate();
  const { refreshAuth } = useAuth();
  const processedRef = useRef(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (processedRef.current) {
      return;
    }
    processedRef.current = true;

    const completeLogin = async () => {
      const searchParams = new URLSearchParams(window.location.search);
      const fragmentParams = getFragmentParams();
      const oauthError = searchParams.get('oauth_error') || fragmentParams.get('error');
      if (oauthError) {
        setError(oauthError);
        return;
      }

      const token = fragmentParams.get('access_token');
      if (!token) {
        setError('Google login did not return a session token.');
        return;
      }

      const redirectPath = sanitizeRedirectPath(fragmentParams.get('redirect'));
      try {
        api.setToken(token);
        await refreshAuth();
        navigate(redirectPath, { replace: true });
      } catch (err) {
        api.clearToken();
        setError(err instanceof Error ? err.message : 'Google login failed');
      } finally {
        window.history.replaceState(null, document.title, window.location.pathname);
      }
    };

    void completeLogin();
  }, [navigate, refreshAuth]);

  return (
    <div className="login-root">
      <div className="oauth-callback-panel">
        {error ? (
          <>
            <AlertCircle size={22} />
            <div className="oauth-callback-title">Sign in failed</div>
            <div className="oauth-callback-copy">{error}</div>
            <Link to="/login" className="oauth-callback-link">
              Back to login
            </Link>
          </>
        ) : (
          <>
            <Loader2 size={24} className="animate-spin" />
            <div className="oauth-callback-title">Finishing sign in</div>
            <div className="oauth-callback-copy">Securing your workspace...</div>
          </>
        )}
      </div>
    </div>
  );
}
