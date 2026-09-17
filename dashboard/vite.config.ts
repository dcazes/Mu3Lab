// Mu3Lab :: dashboard/vite.config.ts
// WHAT: Build config. Outputs a static bundle to dist/ (served by ctl/app.py).
//       Dev proxy forwards /api to the backend so `npm run dev` just works.
// WHY:  No fancy code-splitting yet — one small status screen. Pinned major
//       versions in package.json; `npm ci` reproduces this exactly.
// DEBUG: `npx vite build` writes dist/; `npx tsc --noEmit` type-checks.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8787' } },
});
