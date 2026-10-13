<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1>预约排队</h1>
        <p>{{ auth.isAdmin ? '查看全馆预约队列，处理到书保留与超时释放' : '库存不足时可预约，到书后请在保留期内取书' }}</p>
      </div>
      <el-button type="primary" @click="$router.push('/books')">
        <el-icon><Collection /></el-icon>
        去借书
      </el-button>
    </header>

    <div class="toolbar">
      <el-input
        v-model="query.keyword"
        clearable
        :placeholder="auth.isAdmin ? '书名、ISBN 或读者' : '书名或 ISBN'"
        style="width: 260px"
        @keyup.enter="search"
        @clear="search"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-select
        v-model="query.status"
        clearable
        placeholder="全部状态"
        style="width: 160px"
        @change="search"
      >
        <el-option label="排队中" value="WAITING" />
        <el-option label="待取书" value="READY" />
        <el-option label="已借出" value="COMPLETED" />
        <el-option label="已取消" value="CANCELED" />
        <el-option label="已失效" value="EXPIRED" />
      </el-select>
      <el-button @click="search">
        <el-icon><RefreshLeft /></el-icon>
        查询
      </el-button>
    </div>

    <div v-loading="loading" class="table-panel">
      <el-table :data="records" style="width: 100%">
        <el-table-column label="图书" min-width="220" fixed="left">
          <template #default="{ row }">
            <div class="book-title">
              <strong>{{ row.bookTitle || '图书已删除' }}</strong>
              <span>{{ row.bookIsbn || '-' }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column v-if="auth.isAdmin" label="读者" min-width="130">
          <template #default="{ row }">
            <div class="reader-cell">
              <strong>{{ row.realName || row.username || '-' }}</strong>
              <span>{{ row.username }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="排队序号" width="110" align="center">
          <template #default="{ row }">
            <span v-if="row.status === 'WAITING'">第 {{ row.queuePosition }} 位</span>
            <span v-else-if="row.status === 'READY'" class="ready-text">可借阅</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusMeta(row.status).type" effect="light">
              {{ statusMeta(row.status).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="createdAt" label="预约时间" min-width="170" />
        <el-table-column label="保留至" min-width="170">
          <template #default="{ row }">
            <span v-if="row.status === 'READY'">{{ row.expireAt }}</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button v-if="isActive(row)" link type="primary" @click="handleCancel(row)">
              取消预约
            </el-button>
            <span v-else class="muted">已结束</span>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无预约记录" />
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
import { ElMessage, ElMessageBox } from 'element-plus'
import { cancelReservation, getReservations } from '@/api/reservations'
import { useAuthStore } from '@/store/auth'

const auth = useAuthStore()
const loading = ref(false)
const records = ref([])
const total = ref(0)
const query = reactive({
  current: 1,
  size: 10,
  keyword: '',
  status: '',
})

const STATUS_META = {
  WAITING: { label: '排队中', type: 'warning' },
  READY: { label: '待取书', type: 'success' },
  COMPLETED: { label: '已借出', type: 'info' },
  CANCELED: { label: '已取消', type: 'info' },
  EXPIRED: { label: '已失效', type: 'danger' },
}

function statusMeta(status) {
  return STATUS_META[status] || { label: status, type: 'info' }
}

function isActive(row) {
  return row.status === 'WAITING' || row.status === 'READY'
}

function search() {
  query.current = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const data = await getReservations({
      current: query.current,
      size: query.size,
      keyword: query.keyword || undefined,
      status: query.status || undefined,
    })
    records.value = data.records
    total.value = data.total
  } finally {
    loading.value = false
  }
}

async function handleCancel(row) {
  await ElMessageBox.confirm(
    `确认取消《${row.bookTitle}》的预约吗？`,
    '取消预约',
    {
      confirmButtonText: '确认取消',
      cancelButtonText: '再想想',
      type: 'warning',
    },
  )
  await cancelReservation(row.id)
  ElMessage.success('已取消预约')
  await load()
}

onMounted(load)
</script>

<style scoped>
.reader-cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.reader-cell span {
  color: #879196;
  font-size: 12px;
}

.ready-text {
  color: #25835a;
  font-weight: 600;
}
</style>
