<template>
  <div class="page chat-page">
    <section class="chat-shell">
      <header class="chat-header">
        <div class="chat-identity">
          <span class="chat-mark">
            <el-icon><ChatDotRound /></el-icon>
          </span>
          <div class="chat-title">
            <h1>智能馆员</h1>
            <p>查馆藏 · 查借阅 · 借还预约，写操作需你确认</p>
          </div>
        </div>

        <div class="chat-actions">
          <el-tooltip :content="healthTooltip" placement="bottom">
            <el-tag class="model-tag" :type="modelTag.type" effect="light" round>
              {{ modelTag.text }}
            </el-tag>
          </el-tooltip>
          <el-button class="new-chat" :disabled="!canReset" @click="resetChat">
            <el-icon><Plus /></el-icon>
            新对话
          </el-button>
        </div>
      </header>

      <div v-if="health.error" class="health-warning">
        <el-icon><WarningFilled /></el-icon>
        <span class="health-text">{{ health.error }}</span>
        <el-button link type="danger" @click="loadHealth">重新检测</el-button>
      </div>

      <div ref="scrollRef" class="chat-scroll" @scroll.passive="onScroll">
        <div ref="listRef" class="chat-list">
          <ChatMessage
            v-for="message in messages"
            :key="message.id"
            :message="message"
            @retry="retry"
            @confirm="handleConfirm"
          />
        </div>
        <button v-if="!autoScroll && hasOverflow" type="button" class="scroll-bottom" @click="scrollToBottom">
          <el-icon><ArrowDown /></el-icon>
          回到底部
        </button>
      </div>

      <footer class="chat-composer">
        <div v-if="showQuickQuestions" class="quick-row">
          <button
            v-for="question in QUICK_QUESTIONS"
            :key="question"
            type="button"
            class="quick-chip"
            :disabled="busy"
            @click="send(question)"
          >
            {{ question }}
          </button>
        </div>

        <div class="composer-box" @keydown="onKeydown">
          <el-input
            ref="inputRef"
            v-model="input"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 5 }"
            resize="none"
            maxlength="2000"
            placeholder="想找书、查借阅、还书或预约？直接说就好，例如：帮我找红楼梦"
          />
          <div class="composer-foot">
            <span class="composer-hint">Enter 发送 · Shift + Enter 换行 · AI 生成内容仅供参考</span>
            <div class="composer-buttons">
              <el-button v-if="streaming" type="danger" plain @click="stop">
                <el-icon><CircleClose /></el-icon>
                停止
              </el-button>
              <el-button v-else type="primary" :disabled="!canSend" @click="send()">
                发送
                <el-icon class="el-icon--right"><Promotion /></el-icon>
              </el-button>
            </div>
          </div>
        </div>

        <p v-if="hasPendingConfirmation" class="pending-hint">
          <el-icon><WarningFilled /></el-icon>
          有写操作等待确认，请先在上方点击「确认执行」或「取消」。
        </p>
      </footer>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getHealth, streamChat, streamConfirm } from '@/api/ai'
import { useAuthStore } from '@/store/auth'
import ChatMessage from '@/components/ai/ChatMessage.vue'

const QUICK_QUESTIONS = ['帮我找红楼梦', '我有哪些书快逾期了', '我要还书', '三体还有库存吗']

const auth = useAuthStore()

const scrollRef = ref(null)
const listRef = ref(null)
const inputRef = ref(null)
const input = ref('')
const messages = ref([])
const sessionId = ref('')
const streaming = ref(false)
const autoScroll = ref(true)
const hasOverflow = ref(false)

const health = reactive({
  loading: true,
  ok: false,
  provider: '',
  version: '',
  error: '',
})

let abortController = null
let sequence = 0
let scrollFrame = null
let layoutObserver = null

function nextId() {
  sequence += 1
  return `m-${sequence}`
}

const WELCOME_MARKDOWN = [
  `### 你好，${auth.user?.realName || auth.user?.username || '读者'}`,
  '',
  '我是云阁智能馆员，可以帮你 **查馆藏**、**查借阅**，也能直接办理 **借阅 / 归还 / 预约**。',
  '',
  '涉及写操作时，我会先把要执行的动作列出来，**经你确认后才真正提交**。',
].join('\n')

function createWelcome() {
  return {
    id: nextId(),
    role: 'assistant',
    content: WELCOME_MARKDOWN,
    tools: [],
    confirmations: [],
    meta: {},
    stats: null,
    error: null,
    streaming: false,
    stopped: false,
    welcome: true,
  }
}

function createAssistant() {
  return {
    id: nextId(),
    role: 'assistant',
    content: '',
    tools: [],
    confirmations: [],
    meta: {},
    stats: null,
    error: null,
    streaming: false,
    stopped: false,
    welcome: false,
  }
}

function resetAssistant(message) {
  message.content = ''
  message.tools = []
  message.confirmations = []
  message.meta = {}
  message.stats = null
  message.error = null
  message.stopped = false
  message.streaming = true
}

messages.value = [createWelcome()]

const busy = computed(() => streaming.value)

const hasPendingConfirmation = computed(() =>
  messages.value.some((message) =>
    message.confirmations.some((confirmation) => confirmation.state === 'pending' || confirmation.state === 'submitting'),
  ),
)

const canSend = computed(() => !busy.value && !hasPendingConfirmation.value && input.value.trim().length > 0)

// 新对话随时可点：即使当前只有开场白，点击也是"清空并回到初始态"的幂等操作，
// 不应该因为"还没说过话"就把按钮灰掉（否则点一次后又会变灰，看起来像只能用一次）
const canReset = computed(() => true)

const showQuickQuestions = computed(
  () => !busy.value && !hasPendingConfirmation.value && !messages.value.some((message) => message.role === 'user'),
)

const modelTag = computed(() => {
  if (health.loading) return { type: 'info', text: '模型检测中…' }
  if (!health.ok) return { type: 'danger', text: 'AI 服务未连接' }
  if (health.provider === 'mock') return { type: 'warning', text: '离线 Mock 模式' }
  return { type: 'success', text: health.provider ? `模型：${health.provider}` : '在线模型' }
})

const healthTooltip = computed(() => {
  if (health.loading) return '正在检测 AI 服务'
  if (!health.ok) return health.error || 'AI 服务不可用'
  const base = `llmProvider=${health.provider || '未知'} · version=${health.version || '-'}`
  if (health.provider === 'mock') {
    // 说清楚 mock 不是"AI 坏了"，而是离线演示用的关键词假模型，避免误判
    return `${base}｜当前离线 Mock 模式：只能按内置关键词应答（查馆藏/借还/预约），不理解自由表达。在 .env 配置 LLM_PROVIDER 与 API Key 后重启即切换为真实大模型`
  }
  return base
})

// ---------------------------------------------------------------------------
// 流式请求
// ---------------------------------------------------------------------------
async function runStream(assistant, launcher) {
  if (streaming.value) return
  streaming.value = true
  assistant.streaming = true

  const controller = new AbortController()
  abortController = controller
  let terminal = false

  const onEvent = (event) => {
    if (!event) return
    if (event.event === 'done' || event.event === 'error' || event.event === 'confirm_required') {
      terminal = true
    }
    handleEvent(assistant, event)
  }

  try {
    await launcher({ onEvent, signal: controller.signal })
  } catch (error) {
    if (!controller.signal.aborted) {
      assistant.error = {
        code: error?.code || 'NETWORK_ERROR',
        message: error?.message || '网络异常，请确认 AI 服务可用后重试',
      }
    }
  } finally {
    if (controller.signal.aborted) {
      assistant.stopped = true
    } else if (!terminal && !assistant.error) {
      // 契约要求流必须以 done / error / confirm_required 结束，否则视为连接被截断
      assistant.error = {
        code: 'STREAM_INTERRUPTED',
        message: '连接中断，本次回复可能不完整，请点击重试。',
      }
    }
    assistant.streaming = false
    streaming.value = false
    abortController = null
    scrollToBottomSoon()
  }
}

function handleEvent(message, event) {
  const data = event?.data

  switch (event?.event) {
    case 'meta': {
      const meta = data && typeof data === 'object' ? data : {}
      message.meta = { sessionId: meta.sessionId, traceId: meta.traceId }
      if (meta.sessionId) sessionId.value = meta.sessionId
      break
    }
    case 'token': {
      const text = data && typeof data === 'object' ? data.text : ''
      if (text) {
        message.content += text
        scrollToBottomSoon()
      }
      break
    }
    case 'tool_call': {
      const callId = data?.callId || `tool-${message.tools.length + 1}`
      if (!message.tools.some((tool) => tool.callId === callId)) {
        message.tools.push({
          callId,
          name: data?.name || '',
          args: data?.args || {},
          status: 'running',
          summary: '',
          data: null,
        })
      }
      scrollToBottomSoon()
      break
    }
    case 'tool_result': {
      const callId = data?.callId
      let tool = callId ? message.tools.find((item) => item.callId === callId) : null
      if (!tool) {
        // 理论上 toolCall 一定先到；容错：缺失时补一条
        message.tools.push({
          callId: callId || `tool-${message.tools.length + 1}`,
          name: data?.name || '',
          args: {},
          status: 'running',
          summary: '',
          data: null,
        })
        tool = message.tools[message.tools.length - 1]
      }
      tool.name = data?.name || tool.name
      tool.status = data?.ok ? 'ok' : 'error'
      tool.summary = data?.summary || ''
      tool.data = data && 'data' in data ? data.data : null
      scrollToBottomSoon()
      break
    }
    case 'confirm_required': {
      message.confirmations.push(normalizeConfirmation(data))
      scrollToBottomSoon()
      break
    }
    case 'done': {
      if (data && typeof data === 'object') {
        message.stats = {
          sessionId: data.sessionId,
          usage: data.usage || null,
          latencyMs: data.latencyMs,
        }
        if (data.sessionId) sessionId.value = data.sessionId
      }
      break
    }
    case 'error': {
      message.error = {
        code: data?.code || 'AI_ERROR',
        message: data?.message || 'AI 服务返回未知错误',
      }
      break
    }
    default:
      // 未知事件 / ping 之类：忽略，避免影响渲染
      break
  }
}

/**
 * confirm_required 的两种形态：
 * - 多工具（或标准形态）：{ confirmationId, summary, tools: [{tool,args,summary,callId}] }
 * - 单工具扁平形态：{ confirmationId, summary, tool, args }
 */
function normalizeConfirmation(data) {
  const payload = data && typeof data === 'object' ? data : {}
  let tools = []
  if (Array.isArray(payload.tools) && payload.tools.length) {
    tools = payload.tools.map((tool, index) => ({
      callId: tool?.callId || '',
      tool: tool?.tool || tool?.name || `tool-${index + 1}`,
      args: tool?.args || {},
      summary: tool?.summary || '',
    }))
  } else if (payload.tool) {
    tools = [
      {
        callId: '',
        tool: payload.tool,
        args: payload.args || {},
        summary: payload.summary || '',
      },
    ]
  }
  return {
    confirmationId: payload.confirmationId || '',
    summary: payload.summary || '该操作需要你确认后才能执行。',
    tools,
    state: 'pending',
    outcome: null,
  }
}

// ---------------------------------------------------------------------------
// 发送 / 确认 / 重试 / 停止
// ---------------------------------------------------------------------------
async function send(text) {
  const content = String(typeof text === 'string' ? text : input.value).trim()
  if (!content || busy.value) return
  if (hasPendingConfirmation.value) {
    ElMessage.warning('有写操作等待确认，请先处理后再发送')
    return
  }

  input.value = ''
  messages.value.push({
    id: nextId(),
    role: 'user',
    content,
    tools: [],
    confirmations: [],
    meta: {},
    stats: null,
    error: null,
    streaming: false,
    stopped: false,
    welcome: false,
  })
  messages.value.push(createAssistant())

  // 注意：必须拿到数组里的响应式代理，直接改 push 进去的原始对象不会触发更新
  const assistant = messages.value[messages.value.length - 1]

  autoScroll.value = true
  await nextTick()
  scrollToBottom()

  await runStream(assistant, (context) =>
    streamChat({
      message: content,
      sessionId: sessionId.value || undefined,
      onEvent: context.onEvent,
      signal: context.signal,
    }),
  )
  focusInput()
}

/**
 * 确认卡状态机：pending → submitting → resolved(outcome: approved/rejected/failed)
 * 续流的 token / tool 事件继续写入同一条助手消息。
 */
async function handleConfirm(message, confirmation, approved) {
  if (busy.value || !confirmation || confirmation.state !== 'pending') return
  confirmation.state = 'submitting'
  confirmation.outcome = null
  message.error = null
  autoScroll.value = true

  await runStream(message, (context) =>
    streamConfirm({
      sessionId: sessionId.value,
      confirmationId: confirmation.confirmationId,
      approved,
      onEvent: context.onEvent,
      signal: context.signal,
    }),
  )

  confirmation.state = 'resolved'
  confirmation.outcome = message.error ? 'failed' : approved ? 'approved' : 'rejected'
  scrollToBottomSoon()
}

async function retry(message) {
  if (busy.value) return
  const index = messages.value.findIndex((item) => item.id === message.id)
  if (index < 0) return
  const userMessage = messages.value
    .slice(0, index)
    .reverse()
    .find((item) => item.role === 'user')
  if (!userMessage) return

  resetAssistant(message)
  autoScroll.value = true
  await nextTick()
  scrollToBottom()

  await runStream(message, (context) =>
    streamChat({
      message: userMessage.content,
      sessionId: sessionId.value || undefined,
      onEvent: context.onEvent,
      signal: context.signal,
    }),
  )
  focusInput()
}

function stop() {
  abortController?.abort()
}

function resetChat() {
  if (streaming.value) stop()
  sessionId.value = ''
  // 复用已经渲染过的那条开场白：只清掉用户/助手消息，
  // 这样"开场白"在整个页面生命周期里只出现一次，不会每次新对话都重新生成一条
  const welcome = messages.value.find((message) => message.welcome)
  messages.value = welcome ? [welcome] : [createWelcome()]
  input.value = ''
  autoScroll.value = true
  nextTick(() => {
    scrollToBottom()
    focusInput()
  })
}

// ---------------------------------------------------------------------------
// 输入 / 滚动 / 健康检查
// ---------------------------------------------------------------------------
function onKeydown(event) {
  if (event.key !== 'Enter' || event.shiftKey) return
  // 中文输入法组词中的 Enter 不发送
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  send()
}

function focusInput() {
  try {
    inputRef.value?.focus?.()
  } catch (error) {
    /* 输入框可能已卸载 */
  }
}

function updateScrollMetrics() {
  const el = scrollRef.value
  if (!el) return
  autoScroll.value = el.scrollHeight - el.scrollTop - el.clientHeight < 96
  hasOverflow.value = el.scrollHeight - el.clientHeight > 4
}

function onScroll() {
  updateScrollMetrics()
}

function onWindowResize() {
  updateScrollMetrics()
  // 视口变化后如果原本就贴着底部，继续贴底
  if (autoScroll.value) scrollToBottom()
}

function scrollToBottom() {
  const el = scrollRef.value
  if (el) el.scrollTop = el.scrollHeight
  autoScroll.value = true
  hasOverflow.value = Boolean(el && el.scrollHeight - el.clientHeight > 4)
}

function scrollToBottomSoon() {
  if (!autoScroll.value || scrollFrame) return
  scrollFrame = requestAnimationFrame(() => {
    scrollFrame = null
    scrollToBottom()
  })
}

async function loadHealth() {
  health.loading = true
  health.error = ''
  try {
    const data = await getHealth()
    health.ok = data?.status === 'ok'
    health.provider = data?.llmProvider || ''
    health.version = data?.version || ''
  } catch (error) {
    health.ok = false
    health.provider = ''
    health.error = error?.message || 'AI 服务不可用'
  } finally {
    health.loading = false
  }
}

onMounted(() => {
  loadHealth()
  window.addEventListener('resize', onWindowResize)
  // 视口与消息内容尺寸变化后重新判断是否真的可以滚动（避免 resize 后状态滞后）
  if (typeof ResizeObserver !== 'undefined') {
    layoutObserver = new ResizeObserver(() => {
      updateScrollMetrics()
      if (autoScroll.value) scrollToBottom()
    })
    if (scrollRef.value) layoutObserver.observe(scrollRef.value)
    if (listRef.value) layoutObserver.observe(listRef.value)
  }
  nextTick(() => {
    scrollToBottom()
    focusInput()
  })
})

onBeforeUnmount(() => {
  abortController?.abort()
  if (scrollFrame) cancelAnimationFrame(scrollFrame)
  layoutObserver?.disconnect()
  layoutObserver = null
  window.removeEventListener('resize', onWindowResize)
})
</script>

<style scoped>
.chat-page {
  height: 100%;
}

.chat-shell {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 134px);
  min-height: 540px;
  overflow: hidden;
  border: 1px solid #e2e7e9;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 1px 2px rgba(20, 40, 44, 0.03);
}

.chat-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 14px;
  padding: 14px 20px;
  border-bottom: 1px solid #eaeeee;
  background: #fff;
}

.chat-identity {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 12px;
}

.chat-mark {
  display: grid;
  width: 40px;
  height: 40px;
  flex: 0 0 40px;
  place-items: center;
  border-radius: 9px;
  color: #fff;
  background: #0f766e;
  font-size: 21px;
}

.chat-title {
  min-width: 0;
}

.chat-title h1 {
  margin: 0;
  color: #263137;
  font-size: 17px;
  line-height: 1.25;
}

.chat-title p {
  margin: 4px 0 0;
  color: #8a9398;
  font-size: 12px;
}

.chat-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.model-tag {
  cursor: default;
}

.new-chat {
  flex: 0 0 auto;
}

.health-warning {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 20px;
  border-bottom: 1px solid #f6d9d6;
  color: #a5322e;
  background: #fdf3f2;
  font-size: 12.5px;
}

.health-text {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-scroll {
  position: relative;
  flex: 1;
  overflow-y: auto;
  padding: 20px 20px 10px;
  background: #f7f9fa;
  overscroll-behavior: contain;
}

.chat-list {
  max-width: 980px;
  margin: 0 auto;
}

.scroll-bottom {
  position: sticky;
  bottom: 4px;
  display: flex;
  width: fit-content;
  align-items: center;
  gap: 4px;
  margin: 8px auto 0;
  padding: 6px 14px;
  border: 1px solid #dbe4e4;
  border-radius: 16px;
  color: #4f6662;
  background: #fff;
  box-shadow: 0 2px 8px rgba(24, 44, 46, 0.09);
  font-size: 12px;
  cursor: pointer;
}

.scroll-bottom:hover {
  color: #0f766e;
  border-color: #b9d8d4;
}

.chat-composer {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 20px 14px;
  border-top: 1px solid #eaeeee;
  background: #fff;
}

.quick-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.quick-chip {
  padding: 6px 12px;
  border: 1px solid #dbe4e4;
  border-radius: 14px;
  color: #3f5b57;
  background: #f8fafa;
  font-size: 12px;
  line-height: 1.3;
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
}

.quick-chip:hover:not(:disabled) {
  border-color: #0f766e;
  color: #0f766e;
  background: #eef7f5;
}

.quick-chip:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.composer-box {
  padding: 2px 2px 0;
  border: 1px solid #dde4e5;
  border-radius: 9px;
  background: #fff;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.composer-box:focus-within {
  border-color: #0f766e;
  box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.09);
}

.composer-box :deep(.el-textarea__inner) {
  padding: 9px 11px;
  border: 0;
  box-shadow: none;
  color: #223034;
  background: transparent;
  font-size: 14px;
  line-height: 1.65;
  resize: none;
}

.composer-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 2px 9px 8px;
}

.composer-hint {
  color: #a2acb1;
  font-size: 11.5px;
}

.composer-buttons {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pending-hint {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 0;
  color: #b8791f;
  font-size: 12px;
}

@media (max-width: 800px) {
  .chat-shell {
    height: calc(100vh - 112px);
    min-height: 480px;
  }

  .chat-header {
    gap: 8px;
    padding: 10px 12px;
  }

  .chat-title p {
    display: none;
  }

  .chat-mark {
    width: 34px;
    height: 34px;
    flex-basis: 34px;
    font-size: 18px;
  }

  .chat-scroll {
    padding: 14px 12px 6px;
  }

  .chat-composer {
    padding: 8px 10px 12px;
  }

  .quick-row {
    flex-wrap: nowrap;
    overflow-x: auto;
    padding-bottom: 2px;
  }

  .quick-chip {
    flex: 0 0 auto;
  }

  .composer-hint {
    display: none;
  }

  .composer-foot {
    justify-content: flex-end;
  }
}
</style>
