import { svelte } from '@sveltejs/vite-plugin-svelte'
import { viteSingleFile } from 'vite-plugin-singlefile'
import { defineConfig } from 'vite'

// Builds the whole UI into ONE self-contained file, crm/static/index.html,
// which `crm serve` reads per request (see crm/web.py). JS + CSS are inlined.
export default defineConfig({
  plugins: [svelte(), viteSingleFile()],
  base: './',
  build: {
    outDir: '../crm/static',
    emptyOutDir: true,
  },
  server: {
    // Dev: `crm serve --no-browser` on 8765, then `npm run dev` and open
    // http://localhost:5173/?t=<token>. API calls are proxied to the server.
    proxy: { '/api': 'http://127.0.0.1:8765' },
  },
})
