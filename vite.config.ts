import path from 'path';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  const apiBase = env.VITE_API_BASE || 'http://localhost:5006';
  const proxy: Record<string, string> = {};
  for (const p of ['/auth', '/me', '/users', '/health', '/templates', '/template-versions',
                   '/projects', '/documents', '/seed-example', '/mcp', '/oauth',
                   '/authorize', '/.well-known']) {
    proxy[p] = apiBase;
  }
  return {
    server: { port: 3001, host: '0.0.0.0', proxy },
    plugins: [react()],
    resolve: { alias: { '@': path.resolve(__dirname, '.') } },
  };
});
