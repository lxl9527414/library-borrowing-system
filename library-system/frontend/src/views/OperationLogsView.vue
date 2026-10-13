<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1>操作日志</h1>
        <p>记录关键操作的人员、参数与结果，日志保留最近 90 天</p>
      </div>
    </header>

    <div class="toolbar">
      <el-input
        v-model="query.keyword"
        clearable
        placeholder="操作内容、用户名或接口"
        style="width: 260px"
        @keyup.enter="search"
        @clear="search"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-select
        v-model="query.module"
        clearable
        placeholder="全部模块"
        style="width: 160px"
        @change="search"
      >
        <el-option v-for="item in modules" :key="item" :label="item" :value="item" />
      </el-select>
      <el-button @click="search">
        <el-icon><RefreshLeft /></el-icon>
        查询
      </el-button>
    </div>

    <div v-loading="loading" class="table-panel">
      <el-table :data="records" style="width: 100%">
        <el-table-column prop="createdAt" label="时间" min-width="170" fixed="left" />
        <el-table-column label="操作人" min-width="120">
          <template #default="{ row }">{{ row.username || '-' }}</template>
        </el-table-column>
        <el-table-column label="模块" width="120">
          <template #default="{ row }">
            <el-tag effect="plain">{{ row.module }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="action" label="操作" min-width="130" />
        <el-table-column label="结果" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'SUCCESS' ? 'success' : 'danger'" effect="light">
              {{ row.status === 'SUCCESS' ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="100" align="right">
          <template #default="{ row }">{{ row.durationMs }} ms</template>
        </el-table-column>
        <el-table-column prop="ip" label="IP" min-width="130" show-overflow-tooltip />
        <el-table-column label="详情" width="90" fixed="right">
          <template #default="{ row }">
            <el-popover placement="left" :width="420" trigger="click">
              <template #reference>
                <el-button link type="primary">查看</el-button>
              </template>
              <div class="log-detail">
                <p><strong>接口</strong><span>{{ row.method }} {{ row.uri }}</span></p>
                <p><strong>参数</strong><span>{{ row.params || '-' }}</span></p>
                <p v-if="row.errorMessage"><strong>错误</strong><span>{{ row.errorMessage }}</span></p>
              </div>
            </el-popover>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无操作日志" />
        </template>
      </el-table>
    </div>

    <div class="pagination">
      <el-pagination
        v-model:current-page="query.current"
        v-model:page-size="query.size"
        :total="total"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next, jumper"
        background
        @size-change="load"
        @current-change="load"
      />
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { getLogModules, getOperationLogs } from '@/api/logs'

const loading = ref(false)
const records = ref([])
const modules = ref([])
const total = ref(0)
const query = reactive({
  current: 1,
  size: 10,
  keyword: '',
  module: '',
})

function search() {
  query.current = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const data = await getOperationLogs({
      current: query.current,
      size: query.size,
      keyword: query.keyword || undefined,
      module: query.module || undefined,
    })
    records.value = data.records
    total.value = data.total
  } finally {
    loading.value = false
  }
}

async function loadModules() {
  modules.value = await getLogModules()
}

onMounted(() => {
  load()
  loadModules()
})
</script>

<style scoped>
.log-detail p {
  display: flex;
  gap: 10px;
  margin: 0 0 8px;
  color: #4a565b;
  font-size: 12px;
  line-height: 1.7;
  word-break: break-all;
}

.log-detail p:last-child {
  margin-bottom: 0;
}

.log-detail strong {
  flex: 0 0 40px;
  color: #879196;
  font-weight: 500;
}
</style>
