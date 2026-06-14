/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        base: 'var(--bg-base)',
        surface: 'var(--bg-surface)',
        elevated: 'var(--bg-elevated)',
        border: 'var(--border)',
        accent: 'var(--accent)',
        'accent-yt': 'var(--accent-yt)',
        'accent-tk': 'var(--accent-tk)',
        'accent-ig': 'var(--accent-ig)',
        'text-primary': 'var(--text-primary)',
        'text-muted': 'var(--text-muted)',
        success: 'var(--success)',
        warning: 'var(--warning)',
        error: 'var(--error)',
      },
      borderRadius: {
        card: 'var(--radius-card)',
        btn: 'var(--radius-btn)',
        badge: 'var(--radius-badge)',
      },
      fontFamily: {
        display: ['Space Grotesk', 'sans-serif'],
        ui: ['Inter', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      boxShadow: { card: 'var(--shadow-card)' },
    },
  },
  plugins: [],
}
