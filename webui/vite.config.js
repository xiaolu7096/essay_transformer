import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发态：5173 前端，/api 代理到 8000 后端
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
