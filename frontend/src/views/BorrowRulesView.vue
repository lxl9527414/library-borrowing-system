<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1>借阅规则</h1>
        <p>统一配置借阅上限、借期、续借与逾期罚金，保存后立即生效</p>
      </div>
      <el-button type="primary" :loading="submitting" @click="submit">
        <el-icon><Check /></el-icon>
        保存规则
      </el-button>
    </header>

    <div v-loading="loading" class="rule-panel">
      <el-form ref="formRef" :model="form" label-width="150px">
        <section class="rule-section">
          <h2>借阅设置</h2>
          <div class="rule-grid">
            <el-form-item label="同时借阅上限">
              <el-input-number v-model="form.maxBorrowCount" :min="1" :max="20" />
              <span class="unit">本</span>
            </el-form-item>
            <el-form-item label="借期">
              <el-input-number v-model="form.borrowDays" :min="1" :max="180" />
              <span class="unit">天</span>
            </el-form-item>
            <el-form-item label="最大续借次数">
              <el-input-number v-model="form.maxRenewCount" :min="0" :max="5" />
              <span class="unit">次</span>
            </el-form-item>
            <el-form-item label="每次续借">
              <el-input-number v-model="form.renewDays" :min="1" :max="90" />
              <span class="unit">天</span>
            </el-form-item>
          </div>
        </section>

        <section class="rule-section">
          <h2>逾期罚金</h2>
          <div class="rule-grid">
            <el-form-item label="每日罚金">
              <el-input-number v-model="form.finePerDay" :min="0" :max="999" :precision="2" :step="0.5" />
              <span class="unit">元</span>
            </el-form-item>
            <el-form-item label="罚金上限">
              <el-input-number v-model="form.fineMax" :min="0" :max="9999" :precision="2" :step="5" />
              <span class="unit">元</span>
            </el-form-item>
            <p class="rule-tip">逾期按天向上取整计算，单笔罚金不超过上限；上限为 0 表示不封顶。</p>
          </div>
        </section>

        <section class="rule-section">
          <h2>预约设置</h2>
          <div class="rule-grid">
            <el-form-item label="每人预约上限">
              <el-input-number v-model="form.reserveLimit" :min="0" :max="10" />
              <span class="unit">本</span>
            </el-form-item>
            <el-form-item label="到书保留时长">
              <el-input-number v-model="form.holdDays" :min="1" :max="30" />
              <span class="unit">天</span>
            </el-form-item>
            <p class="rule-tip">库存不足时可预约；到书后按队列通知，超过保留时长自动顺延给下一位读者。</p>
          </div>
        </section>
      </el-form>

      <p class="updated-at">最近更新：{{ updatedAt || '-' }}</p>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getBorrowRule, updateBorrowRule } from '@/api/rules'

const loading = ref(false)
const submitting = ref(false)
const updatedAt = ref('')

const form = reactive({
  maxBorrowCount: 5,
  borrowDays: 30,
  maxRenewCount: 1,
  renewDays: 15,
  finePerDay: 0.2,
  fineMax: 20,
  reserveLimit: 3,
  holdDays: 3,
})

function fill(rule) {
  Object.assign(form, {
    maxBorrowCount: rule.maxBorrowCount,
    borrowDays: rule.borrowDays,
    maxRenewCount: rule.maxRenewCount,
    renewDays: rule.renewDays,
    finePerDay: Number(rule.finePerDay),
    fineMax: Number(rule.fineMax),
    reserveLimit: rule.reserveLimit,
    holdDays: rule.holdDays,
  })
  updatedAt.value = rule.updatedAt
}

async function load() {
  loading.value = true
  try {
    fill(await getBorrowRule())
  } finally {
    loading.value = false
  }
}

async function submit() {
  submitting.value = true
  try {
    fill(await updateBorrowRule({ ...form }))
    ElMessage.success('借阅规则已保存')
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.rule-panel {
  padding: 26px 30px 18px;
  border: 1px solid #e2e7e9;
  border-radius: 8px;
  background: #fff;
}

.rule-section + .rule-section {
  margin-top: 26px;
  padding-top: 22px;
  border-top: 1px solid #eef1f2;
}

.rule-section h2 {
  margin: 0 0 18px;
  color: #263238;
  font-size: 16px;
}

.rule-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 4px 24px;
}

.unit {
  margin-left: 10px;
  color: #7d878c;
  font-size: 13px;
}

.rule-tip {
  grid-column: 1 / -1;
  margin: 6px 0 0;
  color: #879196;
  font-size: 12px;
  line-height: 1.7;
}

.updated-at {
  margin: 16px 0 0;
  color: #9aa4a8;
  font-size: 12px;
}
</style>
