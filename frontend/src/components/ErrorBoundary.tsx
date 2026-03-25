/**
 * Error Boundary Component
 * Catches rendering errors and displays them gracefully
 */

import React, { type ReactNode, type ReactElement } from 'react';
import { AlertCircle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactElement;
  onError?: (error: Error, errorInfo: { componentStack: string }) => void;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
  errorInfo: { componentStack: string } | null;
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
    };
  }

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { hasError: true };
  }

  componentDidCatch(_error: Error, errorInfo: { componentStack: string }) {
    console.error('Error caught by boundary:', _error, errorInfo);
    this.setState({
      error: _error,
      errorInfo,
    });
    this.props.onError?.(_error, errorInfo);
  }

  handleReset = () => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
    });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <div className="flex items-center justify-center min-h-screen bg-background">
          <Card className="w-full max-w-md">
            <CardHeader>
              <div className="flex items-center gap-2">
                <AlertCircle className="h-5 w-5 text-destructive" />
                <CardTitle>Something went wrong</CardTitle>
              </div>
              <CardDescription>
                An unexpected error occurred. Please try again.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {process.env.NODE_ENV === 'development' && this.state.error && (
                <div className="space-y-2 rounded-lg bg-muted p-3 text-sm">
                  <p className="font-mono font-semibold text-destructive">
                    {this.state.error.message}
                  </p>
                  {this.state.errorInfo && (
                    <pre className="overflow-auto text-xs max-h-48 whitespace-pre-wrap break-words">
                      {this.state.errorInfo.componentStack}
                    </pre>
                  )}
                </div>
              )}
              <Button
                onClick={this.handleReset}
                className="w-full"
                size="sm"
              >
                <RefreshCw className="h-4 w-4 mr-2" />
                Try again
              </Button>
            </CardContent>
          </Card>
        </div>
      );
    }

    return this.props.children;
  }
}

/**
 * Hook version of Error Boundary for functional components
 */
export function useErrorHandler() {
  const [error, setError] = React.useState<Error | null>(null);

  const handleError = React.useCallback((err: Error) => {
    console.error('Error handler:', err);
    setError(err);
  }, []);

  const resetError = React.useCallback(() => {
    setError(null);
  }, []);

  return { error, handleError, resetError };
}

/**
 * Component for displaying API errors
 */
export function ApiErrorDisplay({ error, onDismiss }: { error: Error | null; onDismiss: () => void }) {
  if (!error) return null;

  return (
    <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4">
      <div className="flex items-start gap-3">
        <AlertCircle className="h-5 w-5 text-destructive mt-0.5 flex-shrink-0" />
        <div className="flex-1">
          <p className="font-semibold text-destructive">{error.name}</p>
          <p className="text-sm text-destructive/80 mt-1">{error.message}</p>
        </div>
        <button
          onClick={onDismiss}
          className="text-destructive/60 hover:text-destructive"
          aria-label="Dismiss error"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
