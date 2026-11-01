<template>
  <div class="confirm-card" :class="`is-${confirmation.state}`">
    <div class="confirm-head">
      <span class="confirm-icon">
        <el-icon><WarningFilled /></el-icon>
      </span>
      <strong class="confirm-title">执行前请确认</strong>
      <el-tag
        v-if="confirmation.state === 'resolved'"
        size="small"
        effect="light"
        :type="stateTag.type"
        disable-transitions
      >
        {{ stateTag.text }}
      </el-tag>
      <el-tag
        v-else-if="confirmation.state === 'submitting'"
        size="small"
        effect="light"
        type="warning"
        disable-transitions
      >
        执行中…
      </el-tag>
    </div>

    <p class="confirm-summary">{{ confirmation.summary }}</p>

    <ul v-if="confirmation.tools.length" class="confirm-tools">
      <li v-for="(tool, index) in confirmation.tools" :key="tool.callId || `${tool.tool}-${index}`">
        <span class="confirm-tool-name">{{ toolLabel(tool.tool) }}</span>
        <span v-if="toolArgs(tool)" class="confirm-tool-args">{{ toolArgs(tool) }}</span>
        <span
          v-if="tool.summary && tool.summary !== confirmation.summary && confirmation.tools.length > 1"
          class="confirm-tool-tip"
        >
          {{ tool.summary }}
        </span>
      </li>
    </ul>

    <div class="confirm-foot">
      <el-button size="small" :disabled="!canAct" @click="$emit('confirm', false)">取消</el-button>
      <el-button
        size="small"
        type="primary"
        :loading="confirmation.state === 'submitting'"
        :disabled="!canAct"
        @click="$emit('confirm', true)"
      >
        确认执行
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { formatToolArgs, toolLabel } from './toolMeta'

const props = defineProps({
  confirmation: { type: Object, required: true },
})

defineEmits(['confirm'])

// 状态机：pending → submitting → resolved（outcome: approved / rejected / failed）
const canAct = computed(() => props.confirmation.state === 'pending')

const stateTag = computed(() => {
  const outcome = props.confirmation.outcome
  if (outcome === 'approved') return { type: 'success', text: '已确认执行' }
  if (outcome === 'rejected') return { type: 'info', text: '已取消' }
  if (outcome === 'failed') return { type: 'danger', text: '执行失败' }
  return { type: 'info', text: '已处理' }
})

function toolArgs(tool) {
  return formatToolArgs(tool?.args)
}
</script>

<style scoped>
.confirm-card {
  margin-top: 10px;
  padding: 12px 14px;
  border: 1px solid #f0dcb4;
  border-radius: 7px;
  background: #fffaf0;
}

.confirm-head {
  display: flex;
  align-items: center;
  gap: 7px;
}

.confirm-icon {
  display: inline-flex;
  color: #c8872a;
  font-size: 16px;
}

.confirm-title {
  color: #7a5415;
  font-size: 13.5px;
}

.confirm-summary {
  margin: 7px 0 0;
  color: #4a3a1c;
  font-size: 13.5px;
  line-height: 1.65;
  word-break: break-word;
}

.confirm-tools {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
}

.confirm-tools li {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px;
  padding: 4px 0;
  border-top: 1px dashed #f0e2c6;
  font-size: 12.5px;
}

.confirm-tool-name {
  color: #6b5426;
  font-weight: 600;
}

.confirm-tool-args {
  color: #8b7444;
}

.confirm-tool-tip {
  color: #9a8555;
}

.confirm-foot {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
  margin-top: 10px;
}

.is-submitting {
  border-color: #e8c98e;
  background: #fff6e4;
}

.is-resolved {
  border-color: #e6e9e9;
  background: #f8fafa;
}

.is-resolved .confirm-summary {
  color: #6a7479;
}

.is-resolved .confirm-title {
  color: #788489;
}
</style>
