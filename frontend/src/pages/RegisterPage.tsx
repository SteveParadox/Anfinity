/**
 * Register Page Component
 * Refined, architectural signup interface matching login page aesthetic
 */

import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import { Loader2, UserPlus, AlertCircle, Check } from 'lucide-react';

export function RegisterPage() {
  const navigate = useNavigate();
  const { register, isLoading, error, clearError } = useAuth();

  const [formData, setFormData] = useState({
    email: '',
    password: '',
    confirmPassword: '',
    fullName: '',
    agreedToTerms: false,
  });
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});

  const getPasswordStrength = (password: string): { score: number; label: string; color: string } => {
    let score = 0;
    if (password.length >= 8) score++;
    if (password.length >= 12) score++;
    if (/[A-Z]/.test(password)) score++;
    if (/[0-9]/.test(password)) score++;
    if (/[!@#$%^&*]/.test(password)) score++;

    if (score <= 1) return { score: 1, label: 'Weak', color: '#ef4444' };
    if (score <= 2) return { score: 2, label: 'Fair', color: '#eab308' };
    if (score <= 3) return { score: 3, label: 'Good', color: '#3b82f6' };
    return { score: 4, label: 'Strong', color: '#22c55e' };
  };

  const passwordStrength = getPasswordStrength(formData.password);

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

    if (!formData.confirmPassword) {
      errors.confirmPassword = 'Please confirm your password';
    } else if (formData.password !== formData.confirmPassword) {
      errors.confirmPassword = 'Passwords do not match';
    }

    if (!formData.fullName) {
      errors.fullName = 'Full name is required';
    } else if (formData.fullName.length < 2) {
      errors.fullName = 'Full name must be at least 2 characters';
    }

    if (!formData.agreedToTerms) {
      errors.terms = 'You must agree to the terms and conditions';
    }

    setValidationErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();
    if (!validateForm()) return;
    try {
      await register(formData.email, formData.password, formData.fullName);
      navigate('/dashboard');
    } catch {}
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value, type, checked } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value,
    }));
    if (validationErrors[name]) {
      setValidationErrors((prev) => ({ ...prev, [name]: '' }));
    }
  };

  return (
    <div className="login-root">
      {/* Ambient background elements */}
      <div className="orb orb-1" />
      <div className="orb orb-2" />
      <div className="orb orb-3" />
      <div className="grid-overlay" />

      <div className="login-wrapper" style={{ maxWidth: '960px', minHeight: '620px', alignItems: 'stretch' }}>
        {/* Left brand strip */}
        <div className="brand-panel">
          <div>
            <div className="brand-logo-mark">
              <UserPlus size={20} color="#fff" strokeWidth={2} />
            </div>
            <div className="brand-name">Anfinity</div>
            <div className="brand-tagline">AI-Powered Knowledge<br />Operating System</div>
          </div>

          <div className="brand-features">
            {[
              'Semantic search across all your knowledge',
              'Multi-source document ingestion',
              'Real-time AI Q&A on your data',
              'Team collaboration & access controls',
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
        <div className="form-panel" style={{ padding: '44px 48px' }}>
          <div className="form-heading">Create your account</div>
          <div className="form-subheading">Get started with Anfinity — free forever on the starter plan</div>

          {/* Auth error */}
          {error && (
            <div className="error-alert">
              <AlertCircle size={15} />
              <span>{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit}>
            {/* Two-column row: Full Name + Email */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
              <div className="field-group" style={{ marginBottom: 0 }}>
                <label className="field-label">Full Name</label>
                <div className="field-wrap">
                  <input
                    className={`field-input${validationErrors.fullName ? ' error' : ''}`}
                    type="text"
                    name="fullName"
                    placeholder="Jane Smith"
                    value={formData.fullName}
                    onChange={handleInputChange}
                    disabled={isLoading}
                  />
                </div>
                {validationErrors.fullName && (
                  <div className="field-error">
                    <AlertCircle size={11} />
                    {validationErrors.fullName}
                  </div>
                )}
              </div>

              <div className="field-group" style={{ marginBottom: 0 }}>
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
                  />
                </div>
                {validationErrors.email && (
                  <div className="field-error">
                    <AlertCircle size={11} />
                    {validationErrors.email}
                  </div>
                )}
              </div>
            </div>

            {/* Spacer */}
            <div style={{ height: '20px' }} />

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
                />
              </div>
              {formData.password && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '8px' }}>
                  <div style={{
                    flex: 1, height: '3px',
                    background: 'rgba(51,65,85,0.7)',
                    borderRadius: '99px',
                    overflow: 'hidden',
                  }}>
                    <div style={{
                      height: '100%',
                      width: `${(passwordStrength.score / 4) * 100}%`,
                      background: passwordStrength.color,
                      borderRadius: '99px',
                      transition: 'width 0.3s ease, background 0.3s ease',
                    }} />
                  </div>
                  <span style={{ fontSize: '11px', color: passwordStrength.color, fontWeight: 500, minWidth: '36px' }}>
                    {passwordStrength.label}
                  </span>
                </div>
              )}
              {validationErrors.password && (
                <div className="field-error">
                  <AlertCircle size={11} />
                  {validationErrors.password}
                </div>
              )}
            </div>

            {/* Confirm Password */}
            <div className="field-group">
              <label className="field-label">Confirm Password</label>
              <div className="field-wrap" style={{ position: 'relative' }}>
                <input
                  className={`field-input${validationErrors.confirmPassword ? ' error' : ''}`}
                  type="password"
                  name="confirmPassword"
                  placeholder="••••••••"
                  value={formData.confirmPassword}
                  onChange={handleInputChange}
                  disabled={isLoading}
                  style={{ paddingRight: formData.confirmPassword && formData.password === formData.confirmPassword ? '40px' : '14px' }}
                />
                {formData.confirmPassword && formData.password === formData.confirmPassword && (
                  <div style={{
                    position: 'absolute', right: '12px', top: '50%', transform: 'translateY(-50%)',
                    width: '20px', height: '20px', borderRadius: '50%',
                    background: 'rgba(34,197,94,0.15)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}>
                    <Check size={11} color="#22c55e" strokeWidth={3} />
                  </div>
                )}
              </div>
              {validationErrors.confirmPassword && (
                <div className="field-error">
                  <AlertCircle size={11} />
                  {validationErrors.confirmPassword}
                </div>
              )}
            </div>

            {/* Terms checkbox */}
            <div style={{ marginBottom: '4px' }}>
              <label style={{
                display: 'flex', alignItems: 'flex-start', gap: '10px', cursor: 'pointer',
              }}>
                <div style={{ position: 'relative', marginTop: '1px', flexShrink: 0 }}>
                  <input
                    type="checkbox"
                    name="agreedToTerms"
                    checked={formData.agreedToTerms}
                    onChange={handleInputChange}
                    disabled={isLoading}
                    style={{ position: 'absolute', opacity: 0, width: 0, height: 0 }}
                  />
                  <div style={{
                    width: '16px', height: '16px',
                    borderRadius: '4px',
                    border: `1px solid ${validationErrors.terms ? 'rgba(239,68,68,0.5)' : formData.agreedToTerms ? 'rgba(99,102,241,0.7)' : 'rgba(71,85,105,0.5)'}`,
                    background: formData.agreedToTerms ? 'linear-gradient(135deg, #3b82f6, #7c3aed)' : 'rgba(30,41,59,0.8)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'all 0.15s ease',
                    boxShadow: formData.agreedToTerms ? '0 0 0 3px rgba(99,102,241,0.12)' : 'none',
                  }}>
                    {formData.agreedToTerms && <Check size={10} color="#fff" strokeWidth={3} />}
                  </div>
                </div>
                <span style={{ fontSize: '12.5px', color: '#475569', lineHeight: 1.5 }}>
                  I agree to the{' '}
                  <a href="#" style={{ color: '#60a5fa', textDecoration: 'none' }}>Terms of Service</a>
                  {' '}and{' '}
                  <a href="#" style={{ color: '#60a5fa', textDecoration: 'none' }}>Privacy Policy</a>
                </span>
              </label>
              {validationErrors.terms && (
                <div className="field-error" style={{ marginTop: '6px', paddingLeft: '26px' }}>
                  <AlertCircle size={11} />
                  {validationErrors.terms}
                </div>
              )}
            </div>

            {/* Submit */}
            <button type="submit" className="submit-btn" disabled={isLoading}>
              {isLoading ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  Creating account…
                </>
              ) : (
                <>
                  <UserPlus size={15} />
                  Create Account
                </>
              )}
            </button>
          </form>

          {/* Divider + signin */}
          <div className="divider">
            <div className="divider-line" />
            <span className="divider-text">already a member?</span>
            <div className="divider-line" />
          </div>

          <div className="signup-row">
            Already have an account?{' '}
            <Link to="/login" className="signup-link">
              Sign in here
            </Link>
          </div>
        </div>
      </div>

      <div className="page-footer">
        Join thousands of teams using Anfinity for knowledge management
      </div>
    </div>
  );
}