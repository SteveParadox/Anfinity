/**
 * ThemeContext — global theme management
 *
 * Provides theme choice: "dark" | "light" | "system"
 * Provides resolved mode: always "dark" | "light"
 * Syncs with async user settings and system preference changes.
 */

import {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from 'react';
import type { ThemeChoice, ThemeMode } from '../lib/theme';
import {
  THEME_STORAGE_KEY,
  listenToSystemThemeChanges,
  normalizeThemeChoice,
  resolveSystemTheme,
} from '../lib/theme';

export interface ThemeContextType {
  /**
   * User-selected theme choice.
   * Can be "system", which follows OS preference.
   */
  choice: ThemeChoice;
  theme: ThemeChoice;

  /**
   * Effective theme mode.
   * Always either "light" or "dark".
   */
  mode: ThemeMode;
  resolvedTheme: ThemeMode;

  /**
   * True when user is following OS preference.
   */
  isSystem: boolean;

  /**
   * Current OS/browser theme preference.
   */
  systemTheme: ThemeMode;

  /**
   * Set explicit choice: "light", "dark", or "system".
   */
  setChoice: (choice: ThemeChoice) => void;
  setTheme: (choice: ThemeChoice) => void;

  /**
   * Toggle between explicit light/dark.
   * This intentionally exits "system" mode.
   */
  toggleMode: () => void;
  toggleTheme: () => void;
}

export const ThemeContext = createContext<ThemeContextType | null>(null);

interface ThemeContextProviderProps {
  children: React.ReactNode;

  /**
   * Initial theme choice.
   * This may come from user settings.
   */
  initialChoice?: ThemeChoice;

  /**
   * Called whenever the user changes theme choice.
   * Use this to persist to backend/local settings.
   */
  onChoiceChange?: (choice: ThemeChoice) => void;
}

export function ThemeContextProvider({
  children,
  initialChoice,
  onChoiceChange,
}: ThemeContextProviderProps) {
  const [choice, setChoiceState] = useState<ThemeChoice>(() => {
    if (typeof window !== 'undefined') {
      const persisted = window.localStorage.getItem(THEME_STORAGE_KEY);
      if (persisted) return normalizeThemeChoice(persisted);
    }

    return normalizeThemeChoice(initialChoice);
  });

  const [systemTheme, setSystemTheme] = useState<ThemeMode>(() =>
    resolveSystemTheme(),
  );

  /**
   * Important for async settings.
   *
   * Example:
   * - app first renders with "system"
   * - user settings load later with "light"
   * - provider must update
   */
  useEffect(() => {
    if (typeof initialChoice === 'undefined') return;
    setChoiceState(normalizeThemeChoice(initialChoice));
  }, [initialChoice]);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    window.localStorage.setItem(THEME_STORAGE_KEY, choice);
  }, [choice]);

  /**
   * Keep browser/system preference fresh.
   *
   * We listen regardless of current choice because:
   * - the UI may show "System: Dark"
   * - user can switch back to "system" without stale state
   */
  useEffect(() => {
    const unsubscribe = listenToSystemThemeChanges((theme) => {
      setSystemTheme(theme);
    });

    return unsubscribe;
  }, []);

  const mode: ThemeMode = choice === 'system' ? systemTheme : choice;

  const setChoice = useCallback(
    (nextChoice: ThemeChoice) => {
      const safeChoice = normalizeThemeChoice(nextChoice);

      setChoiceState(safeChoice);
      onChoiceChange?.(safeChoice);
    },
    [onChoiceChange],
  );

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const handleStorage = (event: StorageEvent) => {
      if (event.key !== THEME_STORAGE_KEY) return;
      setChoiceState(normalizeThemeChoice(event.newValue));
    };

    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  const toggleMode = useCallback(() => {
    setChoiceState((currentChoice) => {
      const currentMode: ThemeMode =
        currentChoice === 'system' ? systemTheme : currentChoice;

      const nextChoice: ThemeChoice =
        currentMode === 'dark' ? 'light' : 'dark';

      onChoiceChange?.(nextChoice);

      return nextChoice;
    });
  }, [onChoiceChange, systemTheme]);

  const value = useMemo<ThemeContextType>(
    () => ({
      choice,
      theme: choice,
      mode,
      resolvedTheme: mode,
      isSystem: choice === 'system',
      systemTheme,
      setChoice,
      setTheme: setChoice,
      toggleMode,
      toggleTheme: toggleMode,
    }),
    [choice, mode, systemTheme, setChoice, toggleMode],
  );

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  );
}
