import type { Config } from 'tailwindcss'

// Chief Command design system — steel-blue + amber, light.
// Approved 2026-04-20. See:
//   .claude/projects/.../memory/project_chief_ui_design_system.md
//
// Naming strategy: semantic tokens (`surface`, `surface-raised`, `ink`, `muted`,
// `primary`, `accent`, `rail`). Legacy `chief.*` kept as an alias of `primary`
// so existing `bg-chief`/`text-chief` classes keep working without a rename
// sweep across 28 files.
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Backwards-compat alias — steel-blue. Keep `bg-chief` / `text-chief` /
        // `text-chief-light` / `bg-chief-dark` working while surfaces and
        // content migrate to new tokens.
        chief: {
          DEFAULT: '#3b5577',
          dark: '#2c4261',
          light: '#6a83a4',
        },
        // Light canvas. `surface` = app background, `raised` = card/modal,
        // `overlay` = hover/pressed, `border` = 1px divider.
        surface: {
          DEFAULT: '#f6f7f9',
          raised: '#ffffff',
          overlay: '#eef1f5',
          border: '#e3e7ec',
        },
        // Ink = body text. Muted = secondary copy/labels. Used as semantic
        // replacements for the old `text-white/X` opacity ladder.
        ink: {
          DEFAULT: '#15171c',
          muted: '#5c6270',
          subtle: '#8a909d',
        },
        muted: '#5c6270',
        // Primary action color (same as chief).
        primary: {
          DEFAULT: '#3b5577',
          dark: '#2c4261',
          light: '#6a83a4',
        },
        // Amber accent — active states only, NOT backgrounds.
        accent: {
          DEFAULT: '#e8a140',
          dark: '#c98726',
          soft: 'rgba(232,161,64,0.10)',
        },
        // Navy rail chrome — signature inverse-contrast surface.
        rail: {
          DEFAULT: '#1a2230',
          raised: '#232d3f',
          border: '#2a3446',
          ink: '#e6e9ef',
          muted: '#9aa3b4',
        },
        status: {
          online: '#22c55e',
          offline: '#ef4444',
          working: '#f59e0b',
        },
      },
      fontFamily: {
        // Body — variable Inter loaded via @fontsource-variable.
        sans: [
          '"Inter Variable"',
          'Inter',
          'system-ui',
          'sans-serif',
        ],
        // Display — Fraunces. Used for brand, page titles, hero numbers.
        display: [
          '"Fraunces Variable"',
          'Fraunces',
          'Georgia',
          'serif',
        ],
        // Mono — JetBrains Mono for terminal / memory / code surfaces.
        mono: [
          '"JetBrains Mono Variable"',
          '"JetBrains Mono"',
          'ui-monospace',
          'SFMono-Regular',
          'Menlo',
          'monospace',
        ],
      },
      borderRadius: {
        // Design-spec radius scale.
        sm: '6px',
        md: '10px',
        lg: '14px',
        xl: '18px',
        '2xl': '22px',
      },
      boxShadow: {
        card: '0 1px 3px rgba(21,23,28,.05), 0 4px 14px rgba(21,23,28,.04)',
        'card-hover': '0 2px 5px rgba(21,23,28,.07), 0 10px 24px rgba(21,23,28,.07)',
      },
      keyframes: {
        'orb-breathe': {
          '0%, 100%': { transform: 'scale(1)', opacity: '1' },
          '50%': { transform: 'scale(1.06)', opacity: '0.85' },
        },
        'orb-pulse-strong': {
          '0%, 100%': { transform: 'scale(1)', opacity: '1' },
          '40%': { transform: 'scale(1.12)', opacity: '0.95' },
        },
        'orb-ripple': {
          '0%': { transform: 'scale(1)', opacity: '0.5' },
          '100%': { transform: 'scale(1.6)', opacity: '0' },
        },
        'orb-ripple-slow': {
          '0%': { transform: 'scale(1)', opacity: '0.3' },
          '100%': { transform: 'scale(1.9)', opacity: '0' },
        },
      },
      animation: {
        'orb-breathe': 'orb-breathe 3s ease-in-out infinite',
        'orb-pulse-strong': 'orb-pulse-strong 0.8s ease-in-out infinite',
        'orb-ripple': 'orb-ripple 1.8s ease-out infinite',
        'orb-ripple-slow': 'orb-ripple-slow 2.4s ease-out infinite 0.6s',
      },
    },
  },
  plugins: [],
} satisfies Config
