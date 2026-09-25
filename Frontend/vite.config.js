import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/chat': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/health': 'http://127.0.0.1:8000',
      '/recipe-images': 'http://127.0.0.1:8000',
    },
  },
})

