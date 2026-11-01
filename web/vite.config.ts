import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * The bundle is loaded from file:// inside WebKit2GTK.
 *
 * Two constraints drive this config:
 *  1. `base: './'` so asset URLs are relative to index.html.
 *  2. No ES modules at runtime — WebKit refuses `type="module"` over
 *     file:// (CORS), so everything is emitted as one classic IIFE
 *     script and the tag is rewritten in `transformIndexHtml`.
 */
const classicScript = {
  name: 'classic-script-tag',
  transformIndexHtml(html: string) {
    return html
      .replace(/<script type="module" crossorigin/g, '<script defer')
      // `crossorigin` makes the CSS fetch CORS-mode, which file:// rejects.
      .replace(/(<link rel="stylesheet") crossorigin/g, '$1')
  },
}

export default defineConfig({
  base: './',
  plugins: [react(), classicScript],
  build: {
    outDir: '../data/web',
    emptyOutDir: true,
    assetsDir: 'assets',
    cssCodeSplit: false,
    modulePreload: false,
    target: 'es2019',
    sourcemap: false,
    rollupOptions: {
      output: {
        format: 'iife',
        inlineDynamicImports: true,
        entryFileNames: 'assets/app.js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name][extname]',
      },
    },
  },
})