import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  // mode=debug：输出不压缩、带 sourcemap 的产物，只用于阅读/讲解，不参与部署
  const debug = mode === 'debug'
  const env = loadEnv(mode, process.cwd(), '')
  // 本地服务地址，可用 shell 环境变量或 frontend/.env.local 覆盖
  const apiTarget = env.VITE_API_TARGET || process.env.VITE_API_TARGET || 'http://127.0.0.1:18080'
  const aiTarget = env.VITE_AI_TARGET || process.env.VITE_AI_TARGET || 'http://127.0.0.1:8000'

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
        // 顺序敏感：Vite 按 key 声明顺序匹配，`/api/ai` 必须排在 `/api` 之前，
        // 否则 AI 的 SSE 请求会被错误转发到业务后端 18080。
        // http-proxy 逐块 pipe 响应，SSE 不会被整体缓冲。
        '/api/ai': {
          target: aiTarget,
          changeOrigin: true,
        },
        '/api': {
          target: apiTarget,
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
