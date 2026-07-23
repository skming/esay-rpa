import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import wasm from 'vite-plugin-wasm';

export default defineConfig({
  base: './',
  plugins: [wasm(), react(), tailwindcss()],
  resolve: {
    dedupe: ['react', 'react-dom'],
  },
  worker: {
    format: 'es',
    plugins: () => [wasm()],
  },
  optimizeDeps: {
    exclude: ['@silurus/ooxml', 'react-syntax-highlighter'],
  },
  server: {
    port: 19174,
    strictPort: true,
    headers: {
      'Cache-Control': 'no-store'
    },
    proxy: {
      '/api': {
        target: process.env.VITE_RPA_BACKEND_URL ?? 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // Ensure all chunks carry a content hash so old bundles are never reused.
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash].[ext]',
        // Split vendor libraries into stable, independently-cacheable chunks.
        // Each bucket changes only when that library is updated, so Chromium's V8
        // bytecode cache stays valid across app updates for unchanged vendors.
        manualChunks(id) {
          if (!id.includes('node_modules')) return;
          // React runtime — tiny, changes rarely, benefits most from bytecode cache.
          if (id.includes('/react-dom/') || id.includes('/react/') || id.includes('/scheduler/')) {
            return 'vendor-react';
          }
          // ReactFlow + d3 sub-packages it pulls in.
          if (id.includes('@xyflow/') || id.includes('/d3-') || id.includes('/internmap/') || id.includes('/robust-predicates/')) {
            return 'vendor-flow';
          }
          // Radix primitives, Floating UI, Lucide icons — all UI chrome.
          if (id.includes('@radix-ui/') || id.includes('lucide-react') || id.includes('@floating-ui/') || id.includes('cmdk') || id.includes('vaul')) {
            return 'vendor-ui';
          }
          // Markdown pipeline: react-markdown + unified ecosystem.
          if (id.includes('react-markdown') || id.includes('/remark') || id.includes('/rehype') || id.includes('micromark') || id.includes('/mdast') || id.includes('/hast') || id.includes('/unified') || id.includes('/vfile') || id.includes('/unist')) {
            return 'vendor-markdown';
          }
          // Zustand + immer state management.
          if (id.includes('zustand') || id.includes('immer')) {
            return 'vendor-state';
          }
        }
      }
    }
  }
});
