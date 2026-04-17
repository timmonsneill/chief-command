import type { Config } from 'tailwindcss'

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        chief: {
          DEFAULT: '#6366f1',
          dark: '#4f46e5',
          light: '#818cf8',
        },
        surface: {
          DEFAULT: '#0f0f0f',
          raised: '#1a1a1a',
          overlay: '#242424',
          border: '#2e2e2e',
        },
        status: {
          online: '#22c55e',
          offline: '#ef4444',
          working: '#f59e0b',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
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
