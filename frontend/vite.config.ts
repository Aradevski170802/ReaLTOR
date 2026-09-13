/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8000' } },
  },
  preview: {
    port: 4173,
    proxy: { '/api': { target: 'http://127.0.0.1:8000' } },
  },
  build: { outDir: 'dist', chunkSizeWarningLimit: 2500 },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/setupTests.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
  },
});
