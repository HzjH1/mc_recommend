import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import path from 'node:path';

// 产物直接输出到 Django 静态目录：/static/web/*
export default defineConfig({
  plugins: [vue()],
  base: '/static/web/',
  build: {
    outDir: path.resolve(__dirname, '../wxcloudrun/static/web'),
    emptyOutDir: true,
    assetsDir: 'assets',
    sourcemap: false,
  },
});

