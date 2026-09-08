import { afterEach, describe, expect, it, vi } from 'vitest';

describe('normalizeApiBaseUrl', () => {
  afterEach(() => {
    vi.resetModules();
  });

  it('removes trailing slashes from deployed API URLs', async () => {
    Object.defineProperty(globalThis, 'localStorage', {
      value: {
        getItem: () => null,
        setItem: () => undefined,
        removeItem: () => undefined,
      },
      configurable: true,
    });

    const { normalizeApiBaseUrl } = await import('./api');

    expect(normalizeApiBaseUrl('https://anfinity-production.up.railway.app/')).toBe(
      'https://anfinity-production.up.railway.app'
    );
    expect(normalizeApiBaseUrl('  https://api.example.com///  ')).toBe(
      'https://api.example.com'
    );
  });
});
