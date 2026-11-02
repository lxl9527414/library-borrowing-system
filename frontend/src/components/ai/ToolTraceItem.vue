<template>
  <div class="tool-trace" :class="`is-${item.status}`">
    <div class="trace-head">
      <span class="trace-icon">
        <el-icon v-if="item.status === 'running'" class="trace-spin"><Loading /></el-icon>
        <el-icon v-else-if="item.status === 'error'"><CircleCloseFilled /></el-icon>
        <el-icon v-else><CircleCheckFilled /></el-icon>
      </span>
      <span class="trace-title">{{ statusText }}</span>
      <span v-if="argsText" class="trace-args">{{ argsText }}</span>
      <el-tag class="trace-status" size="small" effect="plain" :type="tagType">
        {{ statusLabel }}
      </el-tag>
    </div>

    <p v-if="item.summary" class="trace-summary">{{ item.summary }}</p>

    <button v-if="hasData" type="button" class="trace-toggle" @click="expanded = !expanded">
      <el-icon><ArrowDown v-if="!expanded" /><ArrowUp v-else /></el-icon>
      {{ expanded ? '收起原始结果' : '查看原始结果' }}
    </button>
    <pre v-if="expanded && hasData" class="trace-raw">{{ rawText }}</pre>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { formatRawData, formatToolArgs, toolStatusText } from './toolMeta'

const props = defineProps({
  item: { type: Object, required: true },
})

const expanded = ref(false)

const statusText = computed(() => toolStatusText(props.item.name, props.item.status))
const argsText = computed(() => formatToolArgs(props.item.args))
const hasData = computed(() => props.item.data !== null && props.item.data !== undefined)
const rawText = computed(() => formatRawData(props.item.data))

const statusLabel = computed(() => {
  if (props.item.status === 'ok') return '成功'
  if (props.item.status === 'error') return '失败'
  return '进行中'
})

const tagType = computed(() => {
  if (props.item.status === 'ok') return 'success'
  if (props.item.status === 'error') return 'danger'
  return 'info'
})
</script>

<style scoped>
.tool-trace {
  padding: 9px 12px;
  border: 1px solid #e3e9e9;
  border-left: 3px solid #9fb3b0;
  border-radius: 6px;
  background: #fbfcfc;
}

.tool-trace + .tool-trace {
  margin-top: 8px;
}

.tool-trace.is-ok {
  border-left-color: #3d9b72;
}

.tool-trace.is-error {
  border-left-color: #c4514e;
  background: #fdf8f8;
}

.trace-head {
  display: flex;
  min-width: 0;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.trace-icon {
  display: inline-flex;
  color: #829393;
  font-size: 14px;
}

.tool-trace.is-ok .trace-icon {
  color: #3d9b72;
}

.tool-trace.is-error .trace-icon {
  color: #c4514e;
}

.trace-spin {
  animation: trace-rotate 1s linear infinite;
}

@keyframes trace-rotate {
  to {
    transform: rotate(360deg);
  }
}

.trace-title {
  color: #34454a;
  font-size: 13px;
  font-weight: 600;
}

.trace-args {
  min-width: 0;
  overflow: hidden;
  color: #7d8b90;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trace-status {
  margin-left: auto;
  flex: 0 0 auto;
}

.trace-summary {
  margin: 5px 0 0;
  color: #4c5c61;
  font-size: 12.5px;
  line-height: 1.6;
}

.trace-toggle {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-top: 6px;
  padding: 0;
  border: 0;
  color: #0f766e;
  background: transparent;
  font-size: 12px;
  cursor: pointer;
}

.trace-toggle:hover {
  color: #0b5f59;
  text-decoration: underline;
}

.trace-raw {
  overflow-x: auto;
  max-height: 260px;
  margin: 8px 0 0;
  padding: 10px 12px;
  border-radius: 5px;
  color: #35544f;
  background: #f2f7f6;
  font-family: 'SFMono-Regular', Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
