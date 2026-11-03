/**
 * AI 服务接口（契约 docs/AI-架构与接口契约.md §4）
 *
 * - GET  /api/ai/health          健康检查 / 模型信息
 * - POST /api/ai/chat            SSE 流式对话
 * - POST /api/ai/chat/confirm    写操作确认后恢复同一 session 的 SSE 流
 *
 * 说明：
 * - 统一走 /api/ai 前缀（开发期由 vite 代理到 AI 服务 8000，生产由网关转发）。
 * - SSE 用 fetch + ReadableStream 手工解析，解析细节见 ./sse.js。
 * - 401 行为与 src/api/request.js 保持一致：清理本地登录态并跳转登录页。
 */

import { consumeSSE, parseEventData } from './sse'

const AI_BASE = '/api/ai'
const TOKEN_KEY = 'library_token'
const USER_KEY = 'library_user'

/** 读取本地 token（与 request.js 共用同一套 localStorage key） */
export function getStoredToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || ''
  } catch (error) {
    return ''
  }
}

/** 与 src/api/request.js 的 401 处理保持一致 */
function handleUnauthorized() {
  try {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  } catch (error) {
    /* localStorage 不可用时忽略 */
  }
  if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
    const redirect = encodeURIComponent(window.location.pathname + window.location.search)
    window.location.href = `/login?redirect=${redirect}`
  }
}

function createHttpError(message, code, extra = {}) {
  const error = new Error(message)
  error.code = code
  Object.assign(error, extra)
  return error
}

/**
 * 发起一个 SSE POST 请求并校验响应。
 * @returns {Promise<Response>}
 */
async function openEventStream(path, body, { token, signal } = {}) {
  const authToken = token || getStoredToken()
  let response
  try {
    response = await fetch(`${AI_BASE}${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      },
      body: JSON.stringify(body),
      signal,
    })
  } catch (error) {
    if (error?.name === 'AbortError' || signal?.aborted) {
      // 用户主动中止：交给调用方按正常结束处理
      return null
    }
    throw createHttpError('无法连接 AI 服务，请确认服务已启动后重试', 'NETWORK_ERROR')
  }

  if (response.status === 401) {
    handleUnauthorized()
    throw createHttpError('登录状态已失效，正在跳转登录页…', 'UPSTREAM_UNAUTHENTICATED', { status: 401 })
  }
  if (!response.ok) {
    let detail = ''
    try {
      const payload = await response.json()
      detail = payload?.detail || payload?.message || ''
    } catch (error) {
      /* 响应不是 JSON，用状态码兜底 */
    }
    throw createHttpError(detail || `AI 服务请求失败（HTTP ${response.status}）`, 'AI_HTTP_ERROR', {
      status: response.status,
    })
  }
  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('text/event-stream') && !response.body) {
    throw createHttpError('AI 服务返回了非流式响应', 'AI_BAD_RESPONSE')
  }
  return response
}

/**
 * 把解析器输出转换成 { event, data }，data 已尝试 JSON 解析。
 */
function toHandler(onEvent) {
  return (parsed) => {
    if (typeof onEvent !== 'function') return
    onEvent({ event: parsed.event, data: parseEventData(parsed.data), raw: parsed.data })
  }
}

/**
 * 健康检查：返回 { status, llmProvider, version }
 */
export async function getHealth({ signal } = {}) {
  const response = await fetch(`${AI_BASE}/health`, {
    headers: { Accept: 'application/json' },
    signal,
  })
  if (!response.ok) {
    throw createHttpError(`AI 服务健康检查失败（HTTP ${response.status}）`, 'AI_HEALTH_ERROR', {
      status: response.status,
    })
  }
  return response.json()
}

/**
 * 查询会话当前待确认的写操作（页面刷新 / 确认卡丢失后恢复用）。
 * 返回 { confirmationId, summary, tools } 或 null。
 * @param {object} options
 * @param {string} options.sessionId
 * @param {string} [options.token] 默认读 localStorage
 */
export async function getPendingConfirmation({ sessionId, token } = {}) {
  const authToken = token || getStoredToken()
  const response = await fetch(
    `${AI_BASE}/chat/pending?sessionId=${encodeURIComponent(sessionId || '')}`,
    { headers: { Accept: 'application/json', ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}) } },
  )
  if (response.status === 401) {
    handleUnauthorized()
    throw createHttpError('登录已失效，请重新登录', 'UNAUTHORIZED', { status: 401 })
  }
  if (!response.ok) {
    throw createHttpError(`查询待确认状态失败（HTTP ${response.status}）`, 'PENDING_QUERY_FAILED', {
      status: response.status,
    })
  }
  const body = await response.json().catch(() => null)
  return body?.pending || null
}

/**
 * 流式对话。
 * @param {object} options
 * @param {string} options.message 用户输入
 * @param {string} [options.sessionId] 会话 id（首轮为空，服务端通过 meta 事件下发）
 * @param {string} [options.token] 用户 token（默认读 localStorage）
 * @param {(event: {event: string, data: any, raw: string}) => void} options.onEvent 事件回调
 * @param {AbortSignal} [options.signal] 中止信号
 */
export async function streamChat({ message, sessionId, token, onEvent, signal }) {
  const body = { message }
  if (sessionId) body.sessionId = sessionId
  const response = await openEventStream('/chat', body, { token, signal })
  if (!response) return
  await consumeSSE(response, { onEvent: toHandler(onEvent), signal })
}

/**
 * 写操作确认后恢复流（事件继续回填到同一条助手消息的方式由调用方决定）。
 * @param {object} options
 * @param {string} options.sessionId
 * @param {string} options.confirmationId
 * @param {boolean} options.approved
 * @param {string} [options.token]
 * @param {Function} options.onEvent
 * @param {AbortSignal} [options.signal]
 */
export async function streamConfirm({ sessionId, confirmationId, approved, token, onEvent, signal }) {
  const response = await openEventStream(
    '/chat/confirm',
    { sessionId, confirmationId, approved: Boolean(approved) },
    { token, signal },
  )
  if (!response) return
  await consumeSSE(response, { onEvent: toHandler(onEvent), signal })
}
