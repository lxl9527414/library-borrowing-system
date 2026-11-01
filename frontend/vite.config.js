import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  // mode=debug：输出不压缩、带 sourcemap 的产物，只用于阅读/讲解，不参与部署
  const debug = mode === 'debug'

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      proxy: {
        '/api': {
          target: process.env.VITE_API_TARGET || 'http://127.0.0.1:18080',
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: debug ? 'dist-debug' : '../backend/src/main/resources/static',
      emptyOutDir: true,
      minify: debug ? false : 'esbuild',
      sourcemap: debug,
    },
  }
})
