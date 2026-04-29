import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  THEME_STORAGE_KEY,
  normalizeThemeChoice,
  resolveSystemTheme,
  resolveThemeMode,
} from './theme';

const originalWindow = globalThis.window;

afterEach(() => {
  if (typeof originalWindow === 'undefined') {
    // @ts-expect-error test cleanup
    delete globalThis.window;
    return;
  }

  globalThis.window = originalWindow;
  vi.restoreAllMocks();
});

describe('theme helpers', () => {
  it('normalizes invalid choices to fallback', () => {
    expect(normalizeThemeChoice('dark')).toBe('dark');
    expect(normalizeThemeChoice('light')).toBe('light');
    expect(normalizeThemeChoice('system')).toBe('system');
    expect(normalizeThemeChoice('invalid')).toBe('system');
    expect(normalizeThemeChoice(undefined, 'dark')).toBe('dark');
  });

  it('resolves system theme from matchMedia', () => {
    const mockWindow = {
      matchMedia: vi.fn().mockReturnValue({ matches: true }),
    };

    // @ts-expect-error test stub
    globalThis.window = mockWindow;

    expect(resolveSystemTheme()).toBe('light');
    expect(resolveThemeMode('system')).toBe('light');
    expect(resolveThemeMode('dark')).toBe('dark');
  });

  it('uses stable localStorage key', () => {
    expect(THEME_STORAGE_KEY).toBe('anfinity-theme-choice');
  });
});
