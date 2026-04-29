import { useCallback, useState } from 'react';
import { ApiError, EntitlementError } from '@/lib/api';
import type { EntitlementMetadata } from '@/lib/billing';
import { parseEntitlementMetadata } from '@/lib/billing';

interface UseEntitlementGuardOptions {
  onIntercept?: (metadata: EntitlementMetadata) => void;
}

interface Parsed402Body {
  error?: {
    code?: string;
    message?: string;
    metadata?: Record<string, any>;
  };
}

export function useEntitlementGuard(options: UseEntitlementGuardOptions = {}) {
  const [entitlement, setEntitlement] = useState<EntitlementMetadata | null>(null);

  const dismissUpgradePrompt = useCallback(() => {
    setEntitlement(null);
  }, []);

  const interceptEntitlement = useCallback((metadata: EntitlementMetadata) => {
    setEntitlement(metadata);
    options.onIntercept?.(metadata);
  }, [options]);

  const parse402Response = useCallback(async (response: Response) => {
    let payload: Parsed402Body = {};
    try {
      payload = await response.clone().json();
    } catch {
      payload = {};
    }
    const fallbackError = new EntitlementError(
      payload?.error?.message || 'Upgrade required to continue.',
      payload?.error?.metadata || {},
    );
    const metadata = parseEntitlementMetadata(fallbackError);
    if (metadata) {
      interceptEntitlement(metadata);
    }
    throw fallbackError;
  }, [interceptEntitlement]);

  const guardedFetch = useCallback(async <T>(action: () => Promise<T>): Promise<T> => {
    try {
      const result = await action();
      if (typeof Response !== 'undefined' && result instanceof Response && result.status === 402) {
        await parse402Response(result);
      }
      return result;
    } catch (error) {
      if (error instanceof ApiError && error.status === 402) {
        const metadata = parseEntitlementMetadata(error);
        if (metadata) {
          interceptEntitlement(metadata);
        }
      }
      throw error;
    }
  }, [interceptEntitlement, parse402Response]);

  return {
    guardedFetch,
    entitlement,
    hasEntitlementError: Boolean(entitlement),
    dismissUpgradePrompt,
  };
}
