import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import path from 'node:path';

// 产物直接输出到 Django 静态目录：/static/web/*
export default defineConfig({
  plugins: [vue()],
  base: '/static/web/',
  server: {
    proxy: {
      '/api/v1/meican': {
        target: process.env.VITE_DEV_MEICAN_ORIGIN || process.env.VITE_DEV_BACKEND_ORIGIN || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/api': {
        target: process.env.VITE_DEV_BACKEND_ORIGIN || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: path.resolve(__dirname, '../wxcloudrun/static/web'),
    emptyOutDir: true,
    assetsDir: 'assets',
    sourcemap: false,
  },
});
