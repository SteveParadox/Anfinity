# Frontend Integration Guide

This guide explains how to properly integrate the frontend with the Anfinity backend API in a production-ready manner.

## Table of Contents
1. [Setup](#setup)
2. [Error Handling](#error-handling)
3. [Authentication](#authentication)
4. [Making API Calls](#making-api-calls)
5. [Loading States](#loading-states)
6. [Error Recovery](#error-recovery)
7. [Best Practices](#best-practices)

## Setup

### 1. Environment Configuration
Create a `.env.local` file in your frontend directory:

```bash
# Copy from .env.example
cp .env.example .env.local
```

Configure for your environment:
```env
VITE_API_URL=http://localhost:8000
VITE_API_TIMEOUT=30000
VITE_API_MAX_RETRIES=3
VITE_API_RETRY_DELAY=1000
VITE_DEBUG=true
```

### 2. Initialize API Client
The API client is automatically initialized and exported from `@/lib/api`:

```typescript
import { api, useApi } from '@/lib/api';

// In functional components, use the hook
export function MyComponent() {
  const apiClient = useApi();
  // ...
}
```

### 3. Wrap App with Error Boundary
In `main.tsx` or your root component:

```typescript
import { ErrorBoundary } from '@/components/ErrorBoundary';

export function App() {
  return (
    <ErrorBoundary>
      <YourApp />
    </ErrorBoundary>
  );
}
```

## Error Handling

### Error Classes
The API client provides typed error classes:

```typescript
import {
  ApiError,
  AuthenticationError,
  AuthorizationError,
  ValidationError,
  NotFoundError,
  ServerError
} from '@/lib/api';

try {
  await api.login(credentials);
} catch (error) {
  if (error instanceof ValidationError) {
    console.log('Validation errors:', error.details);
  } else if (error instanceof AuthenticationError) {
    // Redirect to login
  } else if (error instanceof ServerError) {
    // Show retry option
  }
}
```

### Display Errors to Users
```typescript
import { ApiErrorDisplay } from '@/components/ErrorBoundary';

export function MyForm() {
  const [error, setError] = useState<Error | null>(null);

  return (
    <>
      {error && (
        <ApiErrorDisplay
          error={error}
          onDismiss={() => setError(null)}
        />
      )}
      {/* Form content */}
    </>
  );
}
```

## Authentication

### Login Flow
```typescript
import { api } from '@/lib/api';
import { useState } from 'react';

export function LoginForm() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  async function handleLogin(email: string, password: string) {
    try {
      setLoading(true);
      setError(null);

      const response = await api.login({ email, password });
      
      // Token is automatically stored
      // Store additional data (user info, etc.)
      localStorage.setItem('user', JSON.stringify(response.user));

      // Redirect to dashboard
      navigate('/dashboard');
    } catch (err) {
      setError(err as Error);
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={(e) => {
      e.preventDefault();
      handleLogin(email, password);
    }}>
      {/* Form fields */}
    </form>
  );
}
```

### Logout Flow
```typescript
async function handleLogout() {
  await api.logout();
  localStorage.removeItem('user');
  navigate('/login');
}
```

### Token Refresh
The API client automatically handles token refresh when receiving a 401 error:

```typescript
// Behind the scenes:
// 1. API call receives 401
// 2. Token is cleared (stored token was invalid)
// 3. Error is thrown so user can be redirected to login
// 4. User must log in again
```

To implement automatic refresh (optional):
```typescript
// Add to your auth context or app initialization
async function refreshTokenIfNeeded() {
  try {
    const response = await api.refresh();
    api.setToken(response.access_token);
  } catch (error) {
    // Redirect to login
  }
}

// Call periodically or on app focus
setInterval(refreshTokenIfNeeded, 15 * 60 * 1000); // Every 15 minutes
```

## Making API Calls

### Using useApiCall Hook
```typescript
import { useApiCall } from '@/hooks/useApiCall';
import { api } from '@/lib/api';

export function DocumentUpload() {
  const { data, loading, error, execute } = useApiCall<Document>({
    onSuccess: (doc) => console.log('Uploaded:', doc),
    onError: (err) => console.error('Upload failed:', err)
  });

  async function handleUpload(file: File) {
    await execute(() => api.uploadDocument(file, workspaceId));
  }

  return (
    <div>
      <input
        type="file"
        onChange={(e) => {
          if (e.target.files?.[0]) {
            handleUpload(e.target.files[0]);
          }
        }}
      />
      {loading && <Spinner />}
      {error && <ErrorDisplay error={error} />}
    </div>
  );
}
```

### Using usePaginatedApi Hook
```typescript
import { usePaginatedApi } from '@/hooks/useApiCall';
import { api } from '@/lib/api';

export function DocumentList({ workspaceId }: { workspaceId: string }) {
  const { data, loading, page, setPage, pageCount, execute } = usePaginatedApi<Document>({
    onError: (err) => console.error('Load failed:', err)
  });

  useEffect(() => {
    execute((page, pageSize) =>
      api.listDocuments(workspaceId, { page, page_size: pageSize })
    );
  }, [page, execute]);

  return (
    <>
      {data?.map((doc) => (
        <DocumentCard key={doc.id} doc={doc} />
      ))}
      
      <Pagination
        currentPage={page}
        totalPages={pageCount}
        onPageChange={setPage}
      />
    </>
  );
}
```

### Using useApiForm Hook
```typescript
import { useApiForm } from '@/hooks/useApiCall';
import { api } from '@/lib/api';

export function CreateWorkspaceForm() {
  const { loading, error, success, handleSubmit, reset } = useApiForm(
    async (data: { name: string; description?: string }) => {
      return api.createWorkspace(data);
    },
    {
      onSuccess: () => {
        toast.success('Workspace created!');
        navigate('/workspaces');
      },
      onError: (err) => {
        toast.error(err.message);
      }
    }
  );

  return (
    <form onSubmit={async (e) => {
      e.preventDefault();
      await handleSubmit(formData);
    }}>
      {/* Form fields */}
      {loading && <Spinner />}
      {error && <ErrorDisplay error={error} />}
      {success && <SuccessMessage />}
    </form>
  );
}
```

## Loading States

### Component Loading Pattern
```typescript
export function DocumentView() {
  const [document, setDocument] = useState<Document | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    api.getDocument(documentId)
      .then(setDocument)
      .catch(setError)
      .finally(() => setLoading(false));
  }, [documentId]);

  if (loading) return <Skeleton />;
  if (error) return <ErrorDisplay error={error} />;
  if (!document) return null;

  return <DocumentCard document={document} />;
}
```

### Skeleton Loading
```typescript
import { Skeleton } from '@/components/ui/skeleton';

export function DocumentListSkeleton() {
  return (
    <div className="space-y-2">
      {Array(5).fill(0).map((_, i) => (
        <Skeleton key={i} className="h-20" />
      ))}
    </div>
  );
}
```

## Error Recovery

### Retry Logic
```typescript
export function FailedRequest() {
  const { error, execute, retry } = useApiCall();

  async function attemptRequest() {
    await execute(() => api.search('query', workspaceId));
  }

  return (
    <>
      {error && (
        <div className="space-y-2">
          <ApiErrorDisplay error={error} onDismiss={() => {}} />
          <Button onClick={retry}>
            Try Again
          </Button>
        </div>
      )}
    </>
  );
}
```

### Offline Handling
```typescript
export function useOnlineStatus() {
  const [isOnline, setIsOnline] = useState(navigator.onLine);

  useEffect(() => {
    window.addEventListener('online', () => setIsOnline(true));
    window.addEventListener('offline', () => setIsOnline(false));

    return () => {
      window.removeEventListener('online', () => {});
      window.removeEventListener('offline', () => {});
    };
  }, []);

  return isOnline;
}

export function AppContent() {
  const isOnline = useOnlineStatus();

  if (!isOnline) {
    return <OfflineNotice />;
  }

  return <Dashboard />;
}
```

## Best Practices

### 1. Always Handle Errors
```typescript
// ❌ Don't
api.search(query, workspaceId);

// ✅ Do
try {
  const results = await api.search(query, workspaceId);
  setResults(results);
} catch (error) {
  console.error('Search failed:', error);
  setError(error);
}
```

### 2. Use Loading States
```typescript
// ❌ Don't - UI might respond to old data
const results = await api.search(query, workspaceId);

// ✅ Do - Clear previous state
setLoading(true);
try {
  const results = await api.search(query, workspaceId);
  setResults(results);
} finally {
  setLoading(false);
}
```

### 3. Cancel Requests When Unmounting
```typescript
useEffect(() => {
  const controller = new AbortController();

  async function fetchData() {
    try {
      const result = await fetch('/api/data', {
        signal: controller.signal
      });
      // ...
    } catch (error) {
      if (error.name !== 'AbortError') {
        throw error;
      }
    }
  }

  fetchData();

  return () => controller.abort();
}, []);
```

### 4. Validate Input Before API Calls
```typescript
function SearchForm() {
  const [query, setQuery] = useState('');
  const [error, setError] = useState<string | null>(null);

  async function handleSearch() {
    // Validate first
    if (!query.trim()) {
      setError('Query cannot be empty');
      return;
    }

    if (query.length > 2000) {
      setError('Query too long (max 2000 characters)');
      return;
    }

    try {
      const results = await api.search(query, workspaceId);
      setResults(results);
    } catch (err) {
      setError(err.message);
    }
  }

  return <form onSubmit={handleSearch}>{/* ... */}</form>;
}
```

### 5. Implement Request Deduplication
```typescript
export function useApiCache<T>(
  key: string,
  fetcher: () => Promise<T>,
  ttl = 5 * 60 * 1000 // 5 minutes
) {
  const [data, setData] = useState<T | null>(null);
  const cacheRef = useRef<{ data: T; timestamp: number } | null>(null);

  useEffect(() => {
    const now = Date.now();
    if (cacheRef.current && now - cacheRef.current.timestamp < ttl) {
      setData(cacheRef.current.data);
      return;
    }

    fetcher().then((result) => {
      cacheRef.current = { data: result, timestamp: now };
      setData(result);
    });
  }, [key]);

  return data;
}
```

### 6. Use Proper TypeScript Typing
```typescript
// ✅ Do - Fully typed
interface UploadOptions {
  workspaceId: string;
  file: File;
}

async function handleUpload({ workspaceId, file }: UploadOptions): Promise<Document> {
  return api.uploadDocument(file, workspaceId);
}
```

## Troubleshooting

### CORS Errors
```
Access to XMLHttpRequest at 'http://localhost:8000/...' 
from origin 'http://localhost:3000' has been blocked by CORS policy
```

**Solution:** Check backend CORS configuration in `.env`:
```env
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

### Token Invalid
```
Request failed with status code 401
```

**Solution:** User needs to log in again:
```typescript
if (error instanceof AuthenticationError) {
  localStorage.removeItem('token');
  navigate('/login');
}
```

### Rate Limited
```
Request failed with status code 429
```

**Solution:** Implement backoff and retry:
```typescript
const { execute, retry } = useApiCall({ retryCount: 5 });
```

### Timeout
```
Request timeout after 30000ms
```

**Solution:** Increase timeout in `.env.local`:
```env
VITE_API_TIMEOUT=60000
```

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_URL` | `http://localhost:8000` | Backend API base URL |
| `VITE_API_TIMEOUT` | `30000` | Request timeout in milliseconds |
| `VITE_API_MAX_RETRIES` | `3` | Maximum retry attempts |
| `VITE_API_RETRY_DELAY` | `1000` | Retry delay in milliseconds |
| `VITE_DEBUG` | `true` | Debug mode (disable in production) |
