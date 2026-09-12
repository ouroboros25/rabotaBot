/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#0c1210',
        panel: '#131b18',
        panel2: '#18221e',
        line: '#243029',
        accent: '#4fc2ac',
        warn: '#e0a455',
        stop: '#e3836f',
        muted: '#8d9992',
      },
      fontFamily: {
        sans: ['Archivo', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [],
};
