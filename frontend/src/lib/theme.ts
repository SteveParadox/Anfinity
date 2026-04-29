/**
 * Centralized theme definitions for light and dark modes
 * Used by ThemeContext and applied globally across the application
 */

export type ThemeMode = 'light' | 'dark';
export type ThemeChoice = ThemeMode | 'system';
export const THEME_STORAGE_KEY = 'anfinity-theme-choice';

export type ThemeTokens = {
  // Canvas & Surfaces
  canvas: string;
  canvasGlow: string;
  panel: string;
  panelRaised: string;
  
  // Borders
  border: string;
  borderStrong: string;
  
  // Text
  text: string;
  textMuted: string;
  textSubtle: string;
  textInverse: string;
  
  // Accent (primary color)
  accent: string;
  accentSoft: string;
  accentBorder: string;
  
  // Functional colors
  success: string;
  warning: string;
  error: string;
  info: string;
  
  // Effects
  shadow: string;
};

// Dark theme - primary theme
export const DARK_THEME: ThemeTokens = {
  canvas: '#0A0A0A',
  canvasGlow: 'radial-gradient(circle at top left, rgba(245,230,66,0.12), transparent 34%), linear-gradient(180deg, #0A0A0A 0%, #111111 100%)',
  panel: '#111111',
  panelRaised: '#1A1A1A',
  border: '#252525',
  borderStrong: '#3A3A3A',
  text: '#F5F5F5',
  textMuted: '#5A5A5A',
  textSubtle: '#888888',
  textInverse: '#0A0A0A',
  accent: '#F5E642',
  accentSoft: 'rgba(245,230,66,0.14)',
  accentBorder: 'rgba(245,230,66,0.28)',
  success: '#248A58',
  warning: '#B56B00',
  error: '#D92D20',
  info: '#0066CC',
  shadow: '0 18px 50px rgba(0,0,0,0.36)',
};

// Light theme
export const LIGHT_THEME: ThemeTokens = {
  canvas: '#F7F2E8',
  canvasGlow: 'radial-gradient(circle at top left, rgba(166,122,22,0.16), transparent 35%), linear-gradient(180deg, #FFF8E7 0%, #F4EBDD 100%)',
  panel: '#FFFDF7',
  panelRaised: '#F2E8D9',
  border: '#D8C8AF',
  borderStrong: '#BFAE92',
  text: '#17120A',
  textMuted: '#6F604B',
  textSubtle: '#7E705C',
  textInverse: '#FFFDF7',
  accent: '#9A6A00',
  accentSoft: 'rgba(154,106,0,0.12)',
  accentBorder: 'rgba(154,106,0,0.26)',
  success: '#1B8753',
  warning: '#A86D00',
  error: '#C21F1F',
  info: '#0052AD',
  shadow: '0 18px 45px rgba(74,54,20,0.14)',
};

export const THEMES: Record<ThemeMode, ThemeTokens> = {
  dark: DARK_THEME,
  light: LIGHT_THEME,
};

/**
 * Resolve system theme preference from browser
 */
export function resolveSystemTheme(): ThemeMode {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return 'dark';
  }
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

/**
 * Get the effective theme mode
 * If choice is 'system', resolves to the actual system preference
 */
export function resolveThemeMode(choice: ThemeChoice): ThemeMode {
  if (choice === 'system') {
    return resolveSystemTheme();
  }
  return choice;
}

export function isThemeChoice(value: unknown): value is ThemeChoice {
  return value === 'light' || value === 'dark' || value === 'system';
}

export function normalizeThemeChoice(
  value: unknown,
  fallback: ThemeChoice = 'system',
): ThemeChoice {
  return isThemeChoice(value) ? value : fallback;
}

/**
 * Convert theme tokens to CSS custom properties
 */
export function themeTokensToCSSProperties(tokens: ThemeTokens): Record<string, string> {
  return {
    '--theme-canvas': tokens.canvas,
    '--theme-canvas-glow': tokens.canvasGlow,
    '--theme-panel': tokens.panel,
    '--theme-panel-raised': tokens.panelRaised,
    '--theme-border': tokens.border,
    '--theme-border-strong': tokens.borderStrong,
    '--theme-text': tokens.text,
    '--theme-text-muted': tokens.textMuted,
    '--theme-text-subtle': tokens.textSubtle,
    '--theme-text-inverse': tokens.textInverse,
    '--theme-accent': tokens.accent,
    '--theme-accent-soft': tokens.accentSoft,
    '--theme-accent-border': tokens.accentBorder,
    '--theme-success': tokens.success,
    '--theme-warning': tokens.warning,
    '--theme-error': tokens.error,
    '--theme-info': tokens.info,
    '--theme-shadow': tokens.shadow,
  };
}

/**
 * Design tokens that reference CSS custom properties
 * Use these in your inline styles
 */
export const DESIGN_TOKENS = {
  // Canvas & Surfaces
  canvas: 'var(--theme-canvas)',
  canvasGlow: 'var(--theme-canvas-glow)',
  panel: 'var(--theme-panel)',
  panelRaised: 'var(--theme-panel-raised)',
  
  // Borders
  border: 'var(--theme-border)',
  borderStrong: 'var(--theme-border-strong)',
  
  // Text
  text: 'var(--theme-text)',
  textMuted: 'var(--theme-text-muted)',
  textSubtle: 'var(--theme-text-subtle)',
  textInverse: 'var(--theme-text-inverse)',
  
  // Accent
  accent: 'var(--theme-accent)',
  accentSoft: 'var(--theme-accent-soft)',
  accentBorder: 'var(--theme-accent-border)',
  
  // Functional
  success: 'var(--theme-success)',
  warning: 'var(--theme-warning)',
  error: 'var(--theme-error)',
  info: 'var(--theme-info)',
  
  // Effects
  shadow: 'var(--theme-shadow)',
  
  // Font stacks
  fontDisplay: "'Bebas Neue', 'Arial Narrow', sans-serif",
  fontMono: "'IBM Plex Mono', monospace",
  fontBody: "'IBM Plex Sans', sans-serif",
} as const;

/**
 * Listen to system theme changes
 */
export function listenToSystemThemeChanges(callback: (theme: ThemeMode) => void): () => void {
  if (typeof window === 'undefined' || !window.matchMedia) {
    return () => {};
  }
  
  const mediaQuery = window.matchMedia('(prefers-color-scheme: light)');
  
  const handleChange = (e: MediaQueryListEvent) => {
    callback(e.matches ? 'light' : 'dark');
  };
  
  // Modern browsers use addEventListener
  if (mediaQuery.addEventListener) {
    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }
  
  // Fallback for older browsers
  mediaQuery.addListener(handleChange);
  return () => mediaQuery.removeListener(handleChange);
}
