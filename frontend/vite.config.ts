import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig, type PluginOption } from "vite"
import { inspectAttr } from 'kimi-plugin-inspect-react'

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  base: './',
  plugins: [
    command === "serve" ? inspectAttr() : null,
    react(),
  ].filter(Boolean) as PluginOption[],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('commonjsHelpers')) {
            return 'vendor-core';
          }

          if (!id.includes('node_modules')) {
            return undefined;
          }

          if (id.includes('@radix-ui') || id.includes('cmdk') || id.includes('vaul')) {
            return 'vendor-ui';
          }
          if (/[\\/]node_modules[\\/](react|react-dom|react-router-dom|scheduler)[\\/]/.test(id)) {
            return 'vendor-core';
          }
          if (id.includes('framer-motion')) {
            return 'vendor-motion';
          }
          if (id.includes('d3-') || id.includes('react-force-graph')) {
            return 'vendor-graph';
          }
          if (
            id.includes('@tiptap')
            || id.includes('prosemirror')
            || id.includes('yjs')
            || id.includes('y-partykit')
            || id.includes('y-prosemirror')
            || id.includes('y-protocols')
            || id.includes('@hocuspocus')
            || id.includes('partysocket')
          ) {
            return 'vendor-collaboration';
          }
          if (id.includes('lucide-react')) {
            return 'vendor-icons';
          }
          if (id.includes('date-fns')) {
            return 'vendor-date';
          }
          if (id.includes('recharts')) {
            return 'vendor-charts';
          }
          if (id.includes('zod') || id.includes('zustand') || id.includes('clsx') || id.includes('tailwind-merge')) {
            return 'vendor-core';
          }

          return 'vendor-core';
        },
      },
    },
  },
}));
