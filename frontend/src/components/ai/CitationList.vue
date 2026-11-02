<template>
  <section v-if="normalizedChunks.length" class="citation-list" aria-label="引用来源">
    <div class="citation-head">
      <el-icon class="citation-head-icon"><Collection /></el-icon>
      <strong class="citation-head-title">引用来源</strong>
      <span class="citation-head-count">{{ normalizedChunks.length }} 条依据</span>
    </div>

    <ol class="citation-items">
      <li
        v-for="chunk in normalizedChunks"
        :key="chunkKey(chunk)"
        :ref="(el) => setCardRef(chunk.citationNo, el)"
        class="citation-card"
        :class="{ 'is-active': activeNo === chunk.citationNo }"
        :data-citation-no="chunk.citationNo"
      >
        <div class="citation-main">
          <button
            type="button"
            class="citation-no"
            :title="`定位到引用 ${chunk.citationNo}`"
            @click="activate(chunk.citationNo)"
          >
            [{{ chunk.citationNo }}]
          </button>

          <el-tag size="small" effect="plain" :type="typeTone(chunk.sourceType)" class="citation-type">
            {{ sourceTypeLabel(chunk.sourceType) }}
          </el-tag>

          <router-link
            v-if="chunk.link && !chunk.link.external"
            :to="chunk.link.href"
            class="citation-title is-link"
            @click="activate(chunk.citationNo)"
          >
            {{ chunk.title }}
            <el-icon class="citation-open"><ArrowRight /></el-icon>
          </router-link>
          <a
            v-else-if="chunk.link && chunk.link.external"
            :href="chunk.link.href"
            class="citation-title is-link"
            target="_blank"
            rel="noopener noreferrer"
          >
            {{ chunk.title }}
            <el-icon class="citation-open"><TopRight /></el-icon>
          </a>
          <span v-else class="citation-title">{{ chunk.title }}</span>
        </div>

        <p v-if="!expanded[chunk.citationNo]" class="citation-snippet">{{ snippetOf(chunk) }}</p>
        <div v-else class="citation-full">{{ chunk.text }}</div>

        <button
          v-if="isLongText(chunk)"
          type="button"
          class="citation-expand"
          @click="toggle(chunk.citationNo)"
        >
          {{ expanded[chunk.citationNo] ? '收起' : '展开全文' }}
        </button>
      </li>
    </ol>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import {
  citationLink,
  formatChunkSummary,
  normalizeChunks,
  SNIPPET_LIMIT,
  sourceTypeLabel,
} from './citationMeta'

const props = defineProps({
  chunks: { type: Array, default: () => [] },
})

const normalizedChunks = computed(() =>
  normalizeChunks(props.chunks).map((chunk) => ({ ...chunk, link: citationLink(chunk) })),
)

const expanded = reactive({})
const activeNo = ref(null)
const cardEls = new Map()
let activeTimer = null

function chunkKey(chunk) {
  return chunk.chunkId || `cite-${chunk.citationNo}`
}

function setCardRef(no, el) {
  if (el) cardEls.set(no, el)
  else cardEls.delete(no)
}

/** 高亮某条引用（重复点击同一编号会重置计时） */
function activate(no) {
  activeNo.value = no
  if (activeTimer) clearTimeout(activeTimer)
  activeTimer = setTimeout(() => {
    if (activeNo.value === no) activeNo.value = null
  }, 2400)
}

/**
 * 正文 [n] 点击后调用：滚动到对应引用卡片并高亮 + 展开全文。
 * 编号越界（正文写了 chunks 里不存在的 [9]）时静默返回 false，不影响页面。
 */
function focusCitation(no) {
  const target = cardEls.get(no)
  if (!target) return false
  expanded[no] = true
  activate(no)
  try {
    target.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  } catch (error) {
    target.scrollIntoView()
  }
  return true
}

function toggle(no) {
  expanded[no] = !expanded[no]
}

function snippetOf(chunk) {
  return formatChunkSummary(chunk.text, SNIPPET_LIMIT) || '（该片段没有正文内容）'
}

function isLongText(chunk) {
  return String(chunk.text || '').length > SNIPPET_LIMIT
}

function typeTone(type) {
  if (type === 'book') return 'success'
  if (type === 'rule') return 'warning'
  if (type === 'faq') return 'info'
  return 'info'
}

onBeforeUnmount(() => {
  if (activeTimer) clearTimeout(activeTimer)
  cardEls.clear()
})

defineExpose({ focusCitation, activate })
</script>

<style scoped>
.citation-list {
  margin-top: 14px;
  padding: 10px 12px 8px;
  border: 1px solid #dbe7e4;
  border-radius: 8px;
  background: #f8fbfb;
}

.citation-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 7px;
}

.citation-head-icon {
  color: #0f766e;
  font-size: 14px;
}

.citation-head-title {
  color: #2c4a45;
  font-size: 12.5px;
  font-weight: 600;
}

.citation-head-count {
  color: #93a5a1;
  font-size: 11.5px;
}

.citation-items {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.citation-card {
  min-width: 0;
  padding: 7px 9px;
  border: 1px solid #e4edeb;
  border-radius: 6px;
  background: #fff;
  scroll-margin: 14px;
  transition: border-color 0.16s ease, box-shadow 0.16s ease, background 0.16s ease;
}

.citation-card.is-active {
  border-color: #0f766e;
  background: #f2faf8;
  box-shadow: 0 0 0 2px rgba(15, 118, 110, 0.14);
}

.citation-main {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 6px;
}

.citation-no {
  flex: 0 0 auto;
  padding: 0 5px;
  border: 0;
  border-radius: 4px;
  color: #0f766e;
  background: #e6f2f0;
  font-family: 'SFMono-Regular', Menlo, Consolas, monospace;
  font-size: 11px;
  line-height: 18px;
  cursor: pointer;
}

.citation-no:hover {
  background: #d3ebe7;
}

.citation-type {
  flex: 0 0 auto;
}

.citation-title {
  flex: 1 1 150px;
  min-width: 0;
  color: #2b3a3e;
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.55;
  overflow-wrap: anywhere;
  text-decoration: none;
}

.citation-title.is-link {
  color: #0f766e;
  cursor: pointer;
}

.citation-title.is-link:hover {
  text-decoration: underline;
}

.citation-open {
  margin-left: 2px;
  font-size: 11px;
  vertical-align: -1px;
}

.citation-snippet {
  display: -webkit-box;
  overflow: hidden;
  margin: 4px 0 0;
  color: #5f6f74;
  font-size: 12px;
  line-height: 1.6;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.citation-full {
  max-height: 180px;
  overflow-y: auto;
  margin: 4px 0 0;
  padding: 6px 8px;
  border-radius: 5px;
  color: #4b5b60;
  background: #f5f8f8;
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.citation-expand {
  margin-top: 4px;
  padding: 0;
  border: 0;
  color: #0f766e;
  background: transparent;
  font-size: 11.5px;
  cursor: pointer;
}

.citation-expand:hover {
  text-decoration: underline;
}

@media (max-width: 800px) {
  .citation-list {
    padding: 9px 10px 7px;
  }

  .citation-title {
    flex-basis: 100%;
  }

  .citation-full {
    max-height: 140px;
  }
}
</style>
