/**
 * ThemeToggle — accessible light/dark toggle.
 *
 * Manual toggle always exits "system" mode and sets explicit light/dark.
 */

import type { ButtonHTMLAttributes } from 'react';
import { Moon, Sun } from 'lucide-react';
import { useTheme } from '../hooks/useTheme';
import { DESIGN_TOKENS } from '../lib/theme';

interface ThemeToggleProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onClick'> {
  /**
   * Show text label beside icon.
   */
  showLabel?: boolean;

  /**
   * Icon size.
   */
  iconSize?: number;

  /**
   * Compact icon-only mode.
   */
  compact?: boolean;
}

export function ThemeToggle({
  showLabel = false,
  iconSize = 18,
  compact = false,
  disabled,
  style,
  ...buttonProps
}: ThemeToggleProps) {
  const { mode, choice, systemTheme, toggleMode } = useTheme();

  const nextMode = mode === 'dark' ? 'light' : 'dark';

  const label =
    choice === 'system'
      ? `System: ${systemTheme}`
      : mode === 'dark'
        ? 'Dark'
        : 'Light';

  return (
    <button
      {...buttonProps}
      type="button"
      disabled={disabled}
      onClick={toggleMode}
      role="switch"
      aria-checked={mode === 'dark'}
      aria-label={`Switch to ${nextMode} theme`}
      title={`Switch to ${nextMode} theme${
        choice === 'system' ? ' — currently following system preference' : ''
      }`}
      data-theme-toggle
      data-mode={mode}
      data-choice={choice}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: showLabel ? 8 : 0,
        minWidth: compact ? 36 : 'auto',
        minHeight: compact ? 36 : 36,
        padding: compact ? 0 : '7px 12px',
        border: `1px solid ${DESIGN_TOKENS.border}`,
        background: DESIGN_TOKENS.panel,
        color: DESIGN_TOKENS.text,
        borderRadius: 8,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.55 : 1,
        fontSize: 12,
        fontFamily: DESIGN_TOKENS.fontMono,
        letterSpacing: '0.04em',
        fontWeight: 600,
        lineHeight: 1,
        transition:
          'background-color 160ms ease, border-color 160ms ease, color 160ms ease, box-shadow 160ms ease, transform 160ms ease',
        boxShadow: 'none',
        ...style,
      }}
    >
      <span
        aria-hidden="true"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'currentColor',
        }}
      >
        {mode === 'dark' ? (
          <Moon size={iconSize} />
        ) : (
          <Sun size={iconSize} />
        )}
      </span>

      {showLabel && (
        <span
          style={{
            whiteSpace: 'nowrap',
          }}
        >
          {label}
        </span>
      )}
    </button>
  );
}
