import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  base: '/rabota/',
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: true,
    proxy: {
      '/rabota/api': {
        target: 'http://rabota-api:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/rabota\/api/, ''),
      },
    },
  },
});
