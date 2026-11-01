<template>
  <div class="ai-markdown" v-html="rendered"></div>
</template>

<script setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'

// html: false —— 禁止模型输出里的原始 HTML，避免 XSS；markdown-it 会转义尖括号
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

// 外部链接新窗口打开，并加 noopener
const defaultLinkOpen =
  md.renderer.rules.link_open ||
  ((tokens, index, options, env, self) => self.renderToken(tokens, index, options))
md.renderer.rules.link_open = (tokens, index, options, env, self) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, index, options, env, self)
}

const props = defineProps({
  content: { type: String, default: '' },
})

const rendered = computed(() => md.render(props.content || ''))
</script>

<style scoped>
.ai-markdown {
  color: #223034;
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
}

.ai-markdown :deep(> :first-child) {
  margin-top: 0;
}

.ai-markdown :deep(> :last-child) {
  margin-bottom: 0;
}

.ai-markdown :deep(p) {
  margin: 0 0 10px;
}

.ai-markdown :deep(ul),
.ai-markdown :deep(ol) {
  margin: 0 0 10px;
  padding-left: 22px;
}

.ai-markdown :deep(li) {
  margin: 2px 0;
}

.ai-markdown :deep(li > p) {
  margin: 0;
}

.ai-markdown :deep(h1),
.ai-markdown :deep(h2),
.ai-markdown :deep(h3),
.ai-markdown :deep(h4) {
  margin: 16px 0 8px;
  color: #1d2a2e;
  font-size: 15px;
  line-height: 1.4;
}

.ai-markdown :deep(h1) {
  font-size: 17px;
}

.ai-markdown :deep(strong) {
  color: #142022;
  font-weight: 700;
}

.ai-markdown :deep(a) {
  color: #0f766e;
  text-decoration: none;
}

.ai-markdown :deep(a:hover) {
  text-decoration: underline;
}

.ai-markdown :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  color: #a03f2e;
  background: #f2f5f6;
  font-family: 'SFMono-Regular', Menlo, Consolas, monospace;
  font-size: 12.5px;
}

.ai-markdown :deep(pre) {
  overflow-x: auto;
  margin: 0 0 10px;
  padding: 12px 14px;
  border-radius: 6px;
  background: #f5f8f8;
  font-size: 12.5px;
  line-height: 1.6;
}

.ai-markdown :deep(pre code) {
  padding: 0;
  color: #26504a;
  background: transparent;
}

.ai-markdown :deep(blockquote) {
  margin: 0 0 10px;
  padding: 6px 12px;
  border-left: 3px solid #cfe3e0;
  color: #5d6a6f;
  background: #f7fafa;
}

.ai-markdown :deep(table) {
  width: 100%;
  margin: 0 0 10px;
  border-collapse: collapse;
  font-size: 13px;
}

.ai-markdown :deep(th),
.ai-markdown :deep(td) {
  padding: 6px 10px;
  border: 1px solid #e4e9ea;
  text-align: left;
}

.ai-markdown :deep(th) {
  background: #f6f9f9;
  font-weight: 600;
}

.ai-markdown :deep(hr) {
  margin: 14px 0;
  border: 0;
  border-top: 1px solid #e8edee;
}
</style>
