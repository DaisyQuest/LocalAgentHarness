/** @type {import('tailwindcss').Config} */
//
// All palette tokens are CSS variables (see src/styles.css). Tailwind class
// names like ``bg-ink-800`` resolve to ``var(--bg-2)`` so theme switching is
// just toggling ``data-theme`` on <html>.
//
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "var(--bg-0)",
          900: "var(--bg-1)",
          800: "var(--bg-2)",
          700: "var(--bg-3)",
          600: "var(--bg-4)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          soft: "var(--accent-soft)",
        },
        fg: {
          DEFAULT: "var(--fg-base)",
          mute: "var(--fg-mute)",
          dim: "var(--fg-dim)",
        },
        ok: {
          DEFAULT: "var(--success)",
          soft: "var(--success-soft)",
        },
        warn: {
          DEFAULT: "var(--warning)",
          soft: "var(--warning-soft)",
        },
        danger: {
          DEFAULT: "var(--danger)",
          soft: "var(--danger-soft)",
        },
        info: {
          DEFAULT: "var(--info)",
        },
      },
      borderColor: {
        DEFAULT: "var(--border)",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(var(--accent-glow), 0.25), 0 8px 32px -8px rgba(var(--accent-glow), 0.4)",
        "glow-strong": "0 0 24px -4px rgba(var(--accent-glow), 0.55), 0 0 0 1px rgba(var(--accent-glow), 0.4)",
        soft: "0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04)",
      },
    },
  },
  plugins: [],
};
