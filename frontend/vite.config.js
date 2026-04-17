import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig(() => ({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        runtimeCaching: [
          {
            /* Cache forecast API responses: show stale while revalidating */
            urlPattern: /\/api\/forecast/,
            handler: 'StaleWhileRevalidate',
            options: {
              cacheName: 'forecast-api',
              expiration: { maxEntries: 200, maxAgeSeconds: 30 * 60 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            /* Cache color scale, model list, and parameter endpoints */
            urlPattern: /\/api\/(color-scale|models|parameters)/,
            handler: 'CacheFirst',
            options: {
              cacheName: 'meta-api',
              expiration: { maxEntries: 50, maxAgeSeconds: 60 * 60 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            /* Cache map tiles for offline use */
            urlPattern: /^https:\/\/.*basemaps\.cartocdn\.com\//,
            handler: 'CacheFirst',
            options: {
              cacheName: 'map-tiles',
              expiration: { maxEntries: 500, maxAgeSeconds: 7 * 24 * 60 * 60 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
      manifest: {
        name: 'Model Forecast',
        short_name: 'Forecast',
        description: 'Interactive weather model viewer with forecast maps, soundings, and severe weather composites.',
        theme_color: '#06121e',
        background_color: '#06121e',
        display: 'standalone',
        start_url: '/',
        icons: [
          { src: '/favicon.svg', sizes: 'any', type: 'image/svg+xml' },
        ],
      },
    }),
  ],
  base: '/',
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          deckgl: ['@deck.gl/core', '@deck.gl/layers'],
          recharts: ['recharts'],
          leaflet: ['leaflet'],
        },
      },
    },
  },
  server: {
    port: 3002,
    proxy: {
      '/api': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
    },
  },
}))
