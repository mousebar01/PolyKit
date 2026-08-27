/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ['class'],
  content: [
    './src/**/*.{ts,tsx,html}',
    './agent/apps/web/components/**/*.{ts,tsx}',
    './agent/apps/web/hooks/**/*.{ts,tsx}',
    './agent/apps/web/lib/**/*.{ts,tsx}',
  ],
  theme: {
    container: {
      center: true,
      padding: '1.5rem',
      screens: { '2xl': '1400px' },
    },
    extend: {
      colors: {
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
          light: 'hsl(var(--primary))',
          dark: 'hsl(var(--primary) / 0.85)',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        brand: {
          DEFAULT: 'hsl(var(--primary))',
          light: 'hsl(var(--primary))',
          dark: 'hsl(var(--primary) / 0.88)',
          deep: 'hsl(var(--primary) / 0.72)',
        },
        pink: {
          DEFAULT: 'hsl(var(--brand-secondary))',
          light: 'hsl(var(--brand-secondary) / 0.8)',
          dark: 'hsl(var(--brand-secondary) / 0.72)',
        },
        // Compatibility aliases while feature code migrates to semantic tokens.
      surface: {
          50: 'hsl(var(--foreground))',
          100: 'hsl(var(--muted-foreground))',
          200: 'hsl(var(--muted))',
          300: 'hsl(var(--card))',
          400: 'hsl(var(--background))',
          500: 'hsl(var(--background))',
        },
      },
      // Tokens used by the migrated ChatWindow utilities. They are scoped to
      // .agent-chat-root in the PolyKit stylesheet, so the rest of the app
      // keeps its own semantic token values.
      bg: {
        DEFAULT: 'var(--bg)',
        panel: 'var(--bg-panel)',
        hover: 'var(--bg-hover)',
        selected: 'var(--bg-selected)',
        subtle: 'var(--bg-subtle)',
      },
      text: {
        DEFAULT: 'var(--text)',
        muted: 'var(--text-muted)',
        dim: 'var(--text-dim)',
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      fontFamily: {
        sans: ['var(--app-font)'],
        brand: ['var(--brand-font)'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'Liberation Mono', 'Courier New', 'monospace'],
      },
      boxShadow: {
        'glow-brand': '0 0 24px -6px hsl(var(--primary) / 0.35)',
      },
      keyframes: {
        slide: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(400%)' },
        },
      },
      animation: {
        slide: 'slide 1.5s ease-in-out infinite',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
}
