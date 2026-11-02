<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1>借阅记录</h1>
        <p>{{ auth.isAdmin ? '查看全馆借阅与归还记录' : '查看你的借阅与归还记录' }}</p>
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
        :placeholder="auth.isAdmin ? '借阅单号、书名或读者' : '借阅单号或书名'"
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
        style="width: 150px"
        @change="search"
      >
        <el-option label="借阅中" value="BORROWED" />
        <el-option label="已归还" value="RETURNED" />
      </el-select>
      <el-button @click="search">
        <el-icon><RefreshLeft /></el-icon>
        查询
      </el-button>
    </div>

    <div v-loading="loading" class="table-panel">
      <el-table :data="records" style="width: 100%">
        <el-table-column prop="borrowNo" label="借阅单号" min-width="210" fixed="left" />
        <el-table-column label="图书" min-width="210">
          <template #default="{ row }">
            <div class="book-title">
              <strong>{{ row.bookTitle }}</strong>
              <span>{{ row.bookIsbn }}</span>
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
        <el-table-column prop="borrowedAt" label="借出时间" min-width="170" />
        <el-table-column prop="dueAt" label="应还时间" min-width="170" />
        <el-table-column label="续借" width="90" align="center">
          <template #default="{ row }">{{ row.renewCount || 0 }} / {{ maxRenewCount }} 次</template>
        </el-table-column>
        <el-table-column label="罚金" width="150">
          <template #default="{ row }">
            <span v-if="Number(row.fineAmount) > 0" class="fine-amount">
              ¥{{ Number(row.fineAmount).toFixed(2) }}
            </span>
            <span v-else class="muted">-</span>
            <el-tag v-if="row.fineStatus === 'UNPAID'" size="small" type="danger" effect="light">
              待缴纳
            </el-tag>
            <el-tag v-else-if="row.fineStatus === 'PAID'" size="small" type="success" effect="light">
              已缴纳
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.overdue" type="danger" effect="light">已逾期</el-tag>
            <el-tag v-else-if="row.status === 'BORROWED'" type="warning" effect="light">借阅中</el-tag>
            <el-tag v-else type="success" effect="light">已归还</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="归还时间" min-width="170">
          <template #default="{ row }">{{ row.returnedAt || '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="210" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="canRenew(row)"
              link
              type="warning"
              @click="handleRenew(row)"
            >
              续借
            </el-button>
            <el-button
              v-if="row.status === 'BORROWED'"
              link
              type="primary"
              @click="handleReturn(row)"
            >
              归还
            </el-button>
            <el-button
              v-if="auth.isAdmin && row.fineStatus === 'UNPAID'"
              link
              type="danger"
              @click="handleSettleFine(row)"
            >
              缴纳罚金
            </el-button>
            <span v-if="!canRenew(row) && row.status !== 'BORROWED' && row.fineStatus !== 'UNPAID'" class="muted">
              已完成
            </span>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无借阅记录">
            <el-button type="primary" @click="$router.push('/books')">去借书</el-button>
          </el-empty>
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
import { getBorrows, renewBorrow, returnBook, settleFine } from '@/api/borrows'
import { getBorrowRule } from '@/api/rules'
import { useAuthStore } from '@/store/auth'

const auth = useAuthStore()
const loading = ref(false)
const records = ref([])
const total = ref(0)
const maxRenewCount = ref(0)
const query = reactive({
  current: 1,
  size: 10,
  keyword: '',
  status: '',
})

function search() {
  query.current = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const data = await getBorrows({
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

async function loadRule() {
  const rule = await getBorrowRule()
  maxRenewCount.value = rule.maxRenewCount
}

function canRenew(row) {
  return (
    row.status === 'BORROWED' &&
    !row.overdue &&
    Number(row.renewCount || 0) < Number(maxRenewCount.value || 0)
  )
}

async function handleRenew(row) {
  await ElMessageBox.confirm(
    `确认续借《${row.bookTitle}》吗？应还时间将顺延。`,
    '续借确认',
    {
      confirmButtonText: '确认续借',
      cancelButtonText: '取消',
      type: 'info',
    },
  )
  await renewBorrow(row.id)
  ElMessage.success('续借成功')
  await load()
}

async function handleSettleFine(row) {
  await ElMessageBox.confirm(
    `确认收取《${row.bookTitle}》的罚金 ¥${Number(row.fineAmount).toFixed(2)} 吗？`,
    '罚金缴纳',
    {
      confirmButtonText: '确认已缴纳',
      cancelButtonText: '取消',
      type: 'warning',
    },
  )
  await settleFine(row.id)
  ElMessage.success('罚金已结清')
  await load()
}

async function handleReturn(row) {
  await ElMessageBox.confirm(
    `确认归还《${row.bookTitle}》吗？${row.overdue ? '该记录已逾期。' : ''}`,
    '归还确认',
    {
      confirmButtonText: '确认归还',
      cancelButtonText: '取消',
      type: 'info',
    },
  )
  await returnBook(row.id)
  ElMessage.success('归还成功')
  await load()
}

onMounted(() => {
  load()
  loadRule()
})
</script>

<style scoped>
.fine-amount {
  margin-right: 6px;
  color: #c4514e;
  font-weight: 600;
}

.reader-cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.reader-cell span {
  color: #879196;
  font-size: 12px;
}
</style>
