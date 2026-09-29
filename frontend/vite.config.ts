import react from '@vitejs/plugin-react';
import { defineConfig, type Plugin } from 'vite';

// Where `npm run dev` / `npm run preview` forward API calls (the FastAPI app, run locally).
const BACKEND = 'http://127.0.0.1:8000';

// Everything the UI loads comes from its own origin. Only added to the production build:
// the dev server relies on inline scripts and injected styles for hot reload.
const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join('; ');

function contentSecurityPolicy(): Plugin {
  return {
    name: 'attendance-ui:content-security-policy',
    apply: 'build',
    transformIndexHtml: () => [
      {
        tag: 'meta',
        attrs: { 'http-equiv': 'Content-Security-Policy', content: CONTENT_SECURITY_POLICY },
        injectTo: 'head-prepend',
      },
    ],
  };
}

const proxy = { '/api': BACKEND, '/health': BACKEND };

export default defineConfig({
  base: '/ui/',
  plugins: [react(), contentSecurityPolicy()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: { proxy },
  preview: { proxy },
});
