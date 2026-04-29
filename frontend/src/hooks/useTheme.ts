/**
 * useTheme — hook to access and manipulate global theme.
 */

import { useContext } from 'react';
import { ThemeContext } from '../contexts/ThemeContext';

export function useTheme() {
  const context = useContext(ThemeContext);

  if (!context) {
    throw new Error(
      'useTheme() must be used within a <ThemeContextProvider>.',
    );
  }

  return context;
}