<template>
  <article class="chat-message" :class="`is-${message.role}`">
    <div class="msg-avatar" :class="message.role">
      <el-icon v-if="message.role === 'assistant'"><MagicStick /></el-icon>
      <span v-else>{{ avatarText }}</span>
    </div>

    <div class="msg-body">
      <div class="msg-head">
        <span class="msg-author">{{ message.role === 'assistant' ? '智能馆员' : '我' }}</span>
        <span v-if="message.welcome" class="msg-tag">AI 助手</span>
      </div>

      <!-- 用户消息：纯文本渲染，保留换行 -->
      <div v-if="message.role === 'user'" class="msg-bubble user-bubble">{{ message.content }}</div>

      <!-- 助手消息：Markdown + 引用来源 + 工具轨迹 + 确认卡 -->
      <div v-else class="msg-bubble assistant-bubble" @click="onBubbleClick" @keydown="onBubbleKeydown">
        <AiMarkdown v-if="message.content" :content="message.content" :cite="citations.length > 0" />
        <div v-else-if="message.streaming" class="typing-indicator" aria-label="正在生成回复">
          <span></span><span></span><span></span>
        </div>
        <p v-else-if="message.welcome" class="welcome-tip">
          输入你的问题，或点击下方快捷问题开始。
        </p>

        <CitationList v-if="citations.length" ref="citationListRef" :chunks="citations" />
        <p v-else-if="citationHintVisible" class="citation-empty">
          <el-icon><InfoFilled /></el-icon>
          <span>本条回答没有可引用的馆藏依据</span>
        </p>

        <div v-if="message.tools.length" class="trace-list">
          <ToolTraceItem
            v-for="(tool, index) in message.tools"
            :key="tool.callId || `tool-${index}`"
            :item="tool"
          />
        </div>

        <ConfirmCard
          v-for="(confirmation, index) in message.confirmations"
          :key="confirmation.confirmationId || `confirm-${index}`"
          :confirmation="confirmation"
          @confirm="(approved) => $emit('confirm', message, confirmation, approved)"
        />

        <p v-if="message.stopped" class="msg-stopped">
          <el-icon><CircleClose /></el-icon>
          已停止生成
        </p>

        <details v-if="hasTrace" class="msg-trace">
          <summary>{{ metaSummary }}</summary>
          <dl class="msg-trace-list">
            <template v-if="message.meta?.traceId">
              <dt>traceId</dt>
              <dd>{{ message.meta.traceId }}</dd>
            </template>
            <template v-if="message.meta?.sessionId || message.stats?.sessionId">
              <dt>sessionId</dt>
              <dd>{{ message.meta?.sessionId || message.stats?.sessionId }}</dd>
            </template>
            <template v-if="message.stats?.latencyMs !== undefined">
              <dt>latencyMs</dt>
              <dd>{{ message.stats.latencyMs }} ms</dd>
            </template>
            <template v-if="message.stats?.usage">
              <dt>tokens</dt>
              <dd>prompt {{ message.stats.usage.promptTokens ?? 0 }} / completion {{ message.stats.usage.completionTokens ?? 0 }}</dd>
            </template>
          </dl>
        </details>
      </div>

      <!-- 错误气泡 + 重试 -->
      <div v-if="message.error" class="error-bubble">
        <div class="error-head">
          <el-icon><CircleCloseFilled /></el-icon>
          <strong>回复中断</strong>
          <el-tag size="small" type="danger" effect="plain">{{ message.error.code }}</el-tag>
        </div>
        <p class="error-text">{{ message.error.message }}</p>
        <el-button
          v-if="!hasPendingConfirmation && message.error.code !== 'CONFIRMATION_PENDING'"
          class="error-retry"
          size="small"
          plain
          type="danger"
          @click="$emit('retry', message)"
        >
          <el-icon><RefreshRight /></el-icon>
          重试
        </el-button>
        <el-button
          v-else-if="message.error.code === 'CONFIRMATION_PENDING' && !hasPendingConfirmation"
          class="error-retry"
          size="small"
          plain
          type="primary"
          @click="$emit('restart')"
        >
          <el-icon><RefreshRight /></el-icon>
          开始新对话
        </el-button>
      </div>
    </div>
  </article>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useAuthStore } from '@/store/auth'
import AiMarkdown from './AiMarkdown.vue'
import CitationList from './CitationList.vue'
import ConfirmCard from './ConfirmCard.vue'
import ToolTraceItem from './ToolTraceItem.vue'
import { hasCitationMarkers, hasKnowledgeCall, pickCitationChunks } from './citationMeta'

const props = defineProps({
  message: { type: Object, required: true },
})

defineEmits(['retry', 'confirm', 'restart'])

const auth = useAuthStore()

const hasPendingConfirmation = computed(() =>
  (props.message.confirmations || []).some(
    (confirmation) => confirmation.state === 'pending' || confirmation.state === 'submitting',
  ),
)

const avatarText = computed(() => {
  const name = auth.user?.realName || auth.user?.username || '我'
  return String(name).slice(0, 1).toUpperCase()
})

const hasTrace = computed(
  () => Boolean(props.message.meta?.traceId) || Boolean(props.message.stats) || Boolean(props.message.meta?.sessionId),
)

const metaSummary = computed(() => {
  const parts = []
  if (props.message.meta?.traceId) {
    const trace = String(props.message.meta.traceId)
    parts.push(`trace ${trace.slice(0, 10)}`)
  }
  const latency = props.message.stats?.latencyMs
  if (latency !== undefined && latency !== null) parts.push(`${latency}ms`)
  const usage = props.message.stats?.usage
  if (usage) parts.push(`${usage.promptTokens ?? 0}/${usage.completionTokens ?? 0} tokens`)
  if (!parts.length) parts.push('调用详情')
  return parts.join(' · ')
})

// ---------------------------------------------------------------------------
// 引用来源（RAG citation）
// ---------------------------------------------------------------------------
const citationListRef = ref(null)

/** 多次检索时取最后一次“chunks 非空”的 search_knowledge 结果 */
const citations = computed(() => pickCitationChunks(props.message?.tools))

// 调过知识检索（或正文写了 [n]）却没有 chunks 时给一行轻提示；普通闲聊不显示
const citationHintVisible = computed(() => {
  if (citations.value.length) return false
  if (props.message?.streaming) return false
  if (props.message?.welcome) return false
  return hasKnowledgeCall(props.message?.tools) || hasCitationMarkers(props.message?.content)
})

function findCiteMark(event) {
  const target = event?.target
  if (!target || typeof target.closest !== 'function') return null
  const mark = target.closest('.cite-mark')
  if (!mark) return null
  const no = Number(mark.dataset?.no)
  if (!Number.isFinite(no)) return null
  return { mark, no }
}

function highlightMark(bubble, mark) {
  if (!bubble?.querySelectorAll) return
  bubble.querySelectorAll('.cite-mark.is-active').forEach((el) => el.classList.remove('is-active'))
  mark.classList.add('is-active')
}

// v-html 渲染出的 [n] 标记无法直接绑定 Vue 事件，统一在气泡上做事件委托
function onBubbleClick(event) {
  const hit = findCiteMark(event)
  if (!hit) return
  event.preventDefault()
  highlightMark(event.currentTarget, hit.mark)
  // 编号越界（正文写了 chunks 里不存在的 [9]）时静默返回，不报错
  citationListRef.value?.focusCitation?.(hit.no)
}

function onBubbleKeydown(event) {
  if (event.key !== 'Enter' && event.key !== ' ') return
  const hit = findCiteMark(event)
  if (!hit) return
  event.preventDefault()
  highlightMark(event.currentTarget, hit.mark)
  citationListRef.value?.focusCitation?.(hit.no)
}
</script>

<style scoped>
.chat-message {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.chat-message + .chat-message {
  margin-top: 20px;
}

.chat-message.is-user {
  flex-direction: row-reverse;
}

.msg-avatar {
  display: grid;
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  user-select: none;
}

.msg-avatar.assistant {
  background: linear-gradient(135deg, #0f766e, #159a8f);
}

.msg-avatar.user {
  background: #c8872a;
}

.msg-body {
  min-width: 0;
  max-width: min(760px, 84%);
  flex: 0 1 auto;
}

.is-user .msg-body {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.msg-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 5px;
}

.is-user .msg-head {
  flex-direction: row-reverse;
}

.msg-author {
  color: #6b747a;
  font-size: 12px;
}

.msg-tag {
  padding: 0 6px;
  border-radius: 9px;
  color: #0f766e;
  background: #e6f2f0;
  font-size: 10px;
  line-height: 16px;
}

.msg-bubble {
  padding: 11px 14px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
}

.user-bubble {
  border: 1px solid #0f766e;
  border-top-right-radius: 3px;
  color: #fff;
  background: #0f766e;
  white-space: pre-wrap;
}

.assistant-bubble {
  border: 1px solid #e2e8e8;
  border-top-left-radius: 3px;
  color: #223034;
  background: #fff;
}

.welcome-tip {
  margin: 8px 0 0;
  color: #8a9499;
  font-size: 12px;
}

.trace-list {
  margin-top: 10px;
}

.typing-indicator {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 20px;
}

.typing-indicator span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #9db3b0;
  animation: typing-bounce 1.2s infinite ease-in-out;
}

.typing-indicator span:nth-child(2) {
  animation-delay: 0.15s;
}

.typing-indicator span:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes typing-bounce {
  0%,
  60%,
  100% {
    transform: translateY(0);
    opacity: 0.55;
  }
  30% {
    transform: translateY(-4px);
    opacity: 1;
  }
}

.msg-stopped {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 10px 0 0;
  color: #98a2a7;
  font-size: 12px;
}

.msg-trace {
  margin-top: 10px;
  color: #98a2a7;
  font-size: 11.5px;
}

.msg-trace summary {
  width: fit-content;
  cursor: pointer;
  user-select: none;
  list-style: none;
}

.msg-trace summary::-webkit-details-marker {
  display: none;
}

.msg-trace summary::before {
  content: '▸';
  display: inline-block;
  margin-right: 4px;
  transition: transform 0.15s ease;
}

.msg-trace[open] summary::before {
  transform: rotate(90deg);
}

.msg-trace summary:hover {
  color: #6b747a;
}

.msg-trace-list {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 2px 10px;
  margin: 6px 0 0;
  padding: 8px 10px;
  border-radius: 5px;
  background: #f7fafa;
}

.msg-trace-list dt {
  color: #a3adb2;
  font-family: 'SFMono-Regular', Menlo, Consolas, monospace;
}

.msg-trace-list dd {
  overflow-wrap: anywhere;
  margin: 0;
  color: #707c82;
  font-family: 'SFMono-Regular', Menlo, Consolas, monospace;
}

.error-bubble {
  margin-top: 10px;
  padding: 11px 14px;
  border: 1px solid #f2c9c6;
  border-radius: 8px;
  background: #fdf2f1;
}

.error-head {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #a5322e;
  font-size: 13px;
}

.error-text {
  margin: 6px 0 0;
  color: #8c3a36;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-word;
}

.error-retry {
  margin-top: 10px;
}

/* 无引用依据时的轻提示 */
.citation-empty {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 12px 0 0;
  padding: 6px 9px;
  border: 1px dashed #e1e8e8;
  border-radius: 6px;
  color: #8b969b;
  background: #fbfcfc;
  font-size: 12px;
  line-height: 1.5;
}

/* 正文 [n] 引用标记（由 markdownRenderer 生成，样式需穿透到 v-html 内） */
.assistant-bubble :deep(.cite-mark) {
  display: inline-flex;
  min-width: 16px;
  height: 16px;
  align-items: center;
  justify-content: center;
  margin: 0 1px;
  padding: 0 3px;
  border-radius: 4px;
  color: #0f766e;
  background: #e6f2f0;
  font-size: 10.5px;
  font-weight: 600;
  line-height: 1;
  vertical-align: super;
  cursor: pointer;
  user-select: none;
  transition: color 0.15s ease, background 0.15s ease;
}

.assistant-bubble :deep(.cite-mark:hover),
.assistant-bubble :deep(.cite-mark:focus-visible),
.assistant-bubble :deep(.cite-mark.is-active) {
  color: #fff;
  background: #0f766e;
  outline: none;
}

@media (max-width: 800px) {
  .chat-message + .chat-message {
    margin-top: 16px;
  }

  .msg-body {
    max-width: calc(100% - 42px);
  }

  .msg-bubble {
    padding: 10px 12px;
  }
}
</style>
