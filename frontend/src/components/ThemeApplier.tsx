/**
 * ThemeApplier — applies the active theme to the DOM.
 *
 * Responsibilities:
 * - applies CSS custom properties
 * - sets data-theme attributes
 * - sets light/dark classes
 * - updates browser color-scheme
 */

import { useEffect } from 'react';
import { useTheme } from '../hooks/useTheme';
import { THEMES, themeTokensToCSSProperties } from '../lib/theme';

export function ThemeApplier() {
  const { mode, choice, systemTheme } = useTheme();

  useEffect(() => {
    if (typeof document === 'undefined') return;

    const root = document.documentElement;
    const tokens = THEMES[mode];
    const cssProps = themeTokensToCSSProperties(tokens);

    for (const [property, value] of Object.entries(cssProps)) {
      root.style.setProperty(property, value);
    }

    /**
     * Useful for CSS selectors:
     * html[data-theme="dark"] {}
     * html[data-theme-choice="system"] {}
     */
    root.dataset.theme = mode;
    root.dataset.themeChoice = choice;
    root.dataset.systemTheme = systemTheme;

    /**
     * Useful if any part of the app uses Tailwind-style .dark selectors.
     */
    root.classList.toggle('dark', mode === 'dark');
    root.classList.toggle('light', mode === 'light');

    /**
     * Lets native controls/forms/scrollbars adapt.
     */
    root.style.colorScheme = mode;

    /**
     * Optional but helpful for mobile browser chrome.
     */
    const themeColor =
      mode === 'dark' ? tokens.canvas : tokens.panel;

    let metaThemeColor = document.querySelector<HTMLMetaElement>(
      'meta[name="theme-color"]',
    );

    if (!metaThemeColor) {
      metaThemeColor = document.createElement('meta');
      metaThemeColor.name = 'theme-color';
      document.head.appendChild(metaThemeColor);
    }

    metaThemeColor.content = themeColor;
  }, [mode, choice, systemTheme]);

  return null;
}