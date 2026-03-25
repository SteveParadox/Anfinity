/**
 * Login Page Component
 * Refined, architectural login interface with elegant motion and spatial depth
 */

import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Loader2, LogIn, AlertCircle } from 'lucide-react';
import { cn } from '@/lib/utils';

export function LoginPage() {
  const navigate = useNavigate();
  const { login, isLoading, error, clearError } = useAuth();

  const [formData, setFormData] = useState({
    email: '',
    password: '',
  });
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});
  const [focusedField, setFocusedField] = useState<string | null>(null);

  const validateForm = (): boolean => {
    const errors: Record<string, string> = {};
    if (!formData.email) {
      errors.email = 'Email is required';
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(formData.email)) {
      errors.email = 'Invalid email format';
    }
    if (!formData.password) {
      errors.password = 'Password is required';
    } else if (formData.password.length < 8) {
      errors.password = 'Password must be at least 8 characters';
    }
    setValidationErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();
    if (!validateForm()) return;
    try {
      await login(formData.email, formData.password);
      navigate('/dashboard');
    } catch {}
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
    if (validationErrors[name]) {
      setValidationErrors((prev) => ({ ...prev, [name]: '' }));
    }
  };

  return (
    <>

      <div className="login-root">
        {/* Ambient background elements */}
        <div className="orb orb-1" />
        <div className="orb orb-2" />
        <div className="orb orb-3" />
        <div className="grid-overlay" />

        <div className="login-wrapper">
          {/* Left brand strip */}
          <div className="brand-panel">
            <div>
              <div className="brand-logo-mark">
                <LogIn size={20} color="#fff" strokeWidth={2} />
              </div>
              <div className="brand-name">Anfinity</div>
              <div className="brand-tagline">AI-Powered Knowledge<br />Operating System</div>
            </div>

            <div className="brand-features">
              {[
                'Semantic search across all your knowledge',
                'Multi-source document ingestion',
                'Real-time AI Q&A on your data',
              ].map((feat, i) => (
                <div className="brand-feature" key={i}>
                  <div className="brand-feature-dot" />
                  <div className="brand-feature-text">{feat}</div>
                </div>
              ))}
            </div>

            <div className="brand-footer-text">
              Secure · Private · Enterprise-ready
            </div>
          </div>

          {/* Right form panel */}
          <div className="form-panel">
            <div className="form-heading">Welcome back</div>
            <div className="form-subheading">Sign in to your workspace to continue</div>

            {/* Auth error */}
            {error && (
              <div className="error-alert">
                <AlertCircle size={15} />
                <span>{error}</span>
              </div>
            )}

            <form onSubmit={handleSubmit}>
              {/* Email */}
              <div className="field-group">
                <label className="field-label">Email address</label>
                <div className="field-wrap">
                  <input
                    className={`field-input${validationErrors.email ? ' error' : ''}`}
                    type="email"
                    name="email"
                    placeholder="you@example.com"
                    value={formData.email}
                    onChange={handleInputChange}
                    disabled={isLoading}
                    onFocus={() => setFocusedField('email')}
                    onBlur={() => setFocusedField(null)}
                  />
                </div>
                {validationErrors.email && (
                  <div className="field-error">
                    <AlertCircle size={11} />
                    {validationErrors.email}
                  </div>
                )}
              </div>

              {/* Password */}
              <div className="field-group">
                <label className="field-label">Password</label>
                <div className="field-wrap">
                  <input
                    className={`field-input${validationErrors.password ? ' error' : ''}`}
                    type="password"
                    name="password"
                    placeholder="••••••••"
                    value={formData.password}
                    onChange={handleInputChange}
                    disabled={isLoading}
                    onFocus={() => setFocusedField('password')}
                    onBlur={() => setFocusedField(null)}
                  />
                </div>
                {validationErrors.password && (
                  <div className="field-error">
                    <AlertCircle size={11} />
                    {validationErrors.password}
                  </div>
                )}
              </div>

              {/* Submit */}
              <button type="submit" className="submit-btn" disabled={isLoading}>
                {isLoading ? (
                  <>
                    <Loader2 size={16} className="animate-spin" />
                    Signing in…
                  </>
                ) : (
                  <>
                    <LogIn size={15} />
                    Sign In
                  </>
                )}
              </button>
            </form>

            {/* Divider + signup */}
            <div className="divider">
              <div className="divider-line" />
              <span className="divider-text">or</span>
              <div className="divider-line" />
            </div>

            <div className="signup-row">
              Don't have an account?{' '}
              <Link to="/register" className="signup-link">
                Create one free
              </Link>
            </div>
          </div>
        </div>

        <div className="page-footer">
          By signing in you agree to our Terms of Service and Privacy Policy
        </div>
      </div>
    </>
  );
}