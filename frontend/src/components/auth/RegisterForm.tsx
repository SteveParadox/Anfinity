/**
 * Register Form Component
 * Professional registration form for new users
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { AlertCircle, Loader2 } from 'lucide-react';

export function RegisterForm() {
  const navigate = useNavigate();
  const { register, isLoading, error, clearError } = useAuth();

  const [email, setEmail] = useState('');
  const [fullName, setFullName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [acceptTerms, setAcceptTerms] = useState(false);
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});

  /**
   * Validate form inputs
   */
  const validateForm = (): boolean => {
    const errors: Record<string, string> = {};

    if (!fullName.trim()) {
      errors.fullName = 'Full name is required';
    }

    if (!email) {
      errors.email = 'Email is required';
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      errors.email = 'Invalid email address';
    }

    if (!password) {
      errors.password = 'Password is required';
    } else if (password.length < 8) {
      errors.password = 'Password must be at least 8 characters';
    } else if (!/(?=.*[a-z])(?=.*[A-Z])(?=.*\d)/.test(password)) {
      errors.password = 'Password must contain uppercase, lowercase, and numbers';
    }

    if (password !== confirmPassword) {
      errors.confirmPassword = 'Passwords do not match';
    }

    if (!acceptTerms) {
      errors.terms = 'You must accept the terms and conditions';
    }

    setValidationErrors(errors);
    return Object.keys(errors).length === 0;
  };

  /**
   * Handle form submission
   */
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();

    if (!validateForm()) {
      return;
    }

    try {
      await register(email, password, fullName);
      navigate('/');
    } catch {
      // Error is handled by auth context
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-900 to-slate-800 px-4">
      <Card className="w-full max-w-md">
        <CardHeader className="space-y-1">
          <CardTitle className="text-2xl font-bold">Create account</CardTitle>
          <CardDescription>Join Anfinity and start managing your knowledge</CardDescription>
        </CardHeader>

        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Error Alert */}
            {error && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            {/* Full Name Field */}
            <div className="space-y-2">
              <label htmlFor="fullName" className="text-sm font-medium text-slate-700">
                Full Name
              </label>
              <Input
                id="fullName"
                type="text"
                placeholder="John Doe"
                value={fullName}
                onChange={(e) => {
                  setFullName(e.target.value);
                  if (validationErrors.fullName) {
                    setValidationErrors(prev => ({
                      ...prev,
                      fullName: ''
                    }));
                  }
                }}
                disabled={isLoading}
                className={validationErrors.fullName ? 'border-red-500' : ''}
              />
              {validationErrors.fullName && (
                <p className="text-sm text-red-500">{validationErrors.fullName}</p>
              )}
            </div>

            {/* Email Field */}
            <div className="space-y-2">
              <label htmlFor="email" className="text-sm font-medium text-slate-700">
                Email Address
              </label>
              <Input
                id="email"
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value);
                  if (validationErrors.email) {
                    setValidationErrors(prev => ({
                      ...prev,
                      email: ''
                    }));
                  }
                }}
                disabled={isLoading}
                className={validationErrors.email ? 'border-red-500' : ''}
              />
              {validationErrors.email && (
                <p className="text-sm text-red-500">{validationErrors.email}</p>
              )}
            </div>

            {/* Password Field */}
            <div className="space-y-2">
              <label htmlFor="password" className="text-sm font-medium text-slate-700">
                Password
              </label>
              <Input
                id="password"
                type="password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value);
                  if (validationErrors.password) {
                    setValidationErrors(prev => ({
                      ...prev,
                      password: ''
                    }));
                  }
                }}
                disabled={isLoading}
                className={validationErrors.password ? 'border-red-500' : ''}
              />
              {validationErrors.password && (
                <p className="text-sm text-red-500 text-xs">{validationErrors.password}</p>
              )}
              <p className="text-xs text-slate-500">
                At least 8 characters with uppercase, lowercase, and numbers
              </p>
            </div>

            {/* Confirm Password Field */}
            <div className="space-y-2">
              <label htmlFor="confirmPassword" className="text-sm font-medium text-slate-700">
                Confirm Password
              </label>
              <Input
                id="confirmPassword"
                type="password"
                placeholder="••••••••"
                value={confirmPassword}
                onChange={(e) => {
                  setConfirmPassword(e.target.value);
                  if (validationErrors.confirmPassword) {
                    setValidationErrors(prev => ({
                      ...prev,
                      confirmPassword: ''
                    }));
                  }
                }}
                disabled={isLoading}
                className={validationErrors.confirmPassword ? 'border-red-500' : ''}
              />
              {validationErrors.confirmPassword && (
                <p className="text-sm text-red-500">{validationErrors.confirmPassword}</p>
              )}
            </div>

            {/* Terms Checkbox */}
            <div className="flex items-center space-x-2">
              <input
                id="terms"
                type="checkbox"
                checked={acceptTerms}
                onChange={(e) => {
                  setAcceptTerms(e.target.checked);
                  if (validationErrors.terms) {
                    setValidationErrors(prev => ({
                      ...prev,
                      terms: ''
                    }));
                  }
                }}
                disabled={isLoading}
                className="rounded border-gray-300"
              />
              <label htmlFor="terms" className="text-sm text-slate-600">
                I agree to the{' '}
                <a href="/terms" className="text-blue-600 hover:underline">
                  Terms of Service
                </a>
                {' '}and{' '}
                <a href="/privacy" className="text-blue-600 hover:underline">
                  Privacy Policy
                </a>
              </label>
            </div>
            {validationErrors.terms && (
              <p className="text-sm text-red-500">{validationErrors.terms}</p>
            )}

            {/* Submit Button */}
            <Button
              type="submit"
              className="w-full"
              disabled={isLoading}
              size="lg"
            >
              {isLoading ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Creating account...
                </>
              ) : (
                'Create Account'
              )}
            </Button>

            {/* Login Link */}
            <div className="text-center text-sm text-slate-600">
              Already have an account?{' '}
              <button
                type="button"
                onClick={() => navigate('/login')}
                className="font-medium text-blue-600 hover:text-blue-700 underline"
              >
                Sign in
              </button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
