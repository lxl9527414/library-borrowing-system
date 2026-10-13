<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1>工作台</h1>
        <p>{{ today }} · 图书借阅情况总览</p>
      </div>
      <el-button type="primary" @click="router.push(auth.isAdmin ? '/books' : '/borrows')">
        <el-icon><Plus /></el-icon>
        {{ auth.isAdmin ? '管理图书' : '查看我的借阅' }}
      </el-button>
    </header>

    <div v-loading="loading" class="stat-grid">
      <article v-for="item in stats" :key="item.label" class="stat-card" :class="item.tone">
        <div class="stat-icon">
          <el-icon><component :is="item.icon" /></el-icon>
        </div>
        <div class="stat-content">
          <span>{{ item.label }}</span>
          <strong>{{ item.value }}</strong>
        </div>
      </article>
    </div>

    <section class="recent-section">
      <div class="section-heading">
        <div>
          <h2>最近借阅</h2>
          <p>{{ auth.isAdmin ? '全馆最近的借阅动态' : '你的近期借阅动态' }}</p>
        </div>
        <el-button text type="primary" @click="router.push('/borrows')">
          查看全部
          <el-icon class="el-icon--right"><ArrowRight /></el-icon>
        </el-button>
      </div>

      <div class="table-panel">
        <el-table :data="overview?.recentRecords || []" style="width: 100%">
          <el-table-column label="图书" min-width="220">
            <template #default="{ row }">
              <div class="book-title">
                <strong>{{ row.bookTitle }}</strong>
                <span>{{ row.bookIsbn }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column v-if="auth.isAdmin" prop="realName" label="读者" min-width="110">
            <template #default="{ row }">{{ row.realName || row.username || '-' }}</template>
          </el-table-column>
          <el-table-column prop="borrowedAt" label="借出时间" min-width="170" />
          <el-table-column prop="dueAt" label="应还时间" min-width="170" />
          <el-table-column label="状态" width="110" fixed="right">
            <template #default="{ row }">
              <el-tag v-if="row.overdue" type="danger" effect="light">已逾期</el-tag>
              <el-tag v-else-if="row.status === 'BORROWED'" type="warning" effect="light">借阅中</el-tag>
              <el-tag v-else type="success" effect="light">已归还</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getDashboard } from '@/api/dashboard'
import { useAuthStore } from '@/store/auth'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)
const overview = ref(null)

const today = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: 'long',
  day: 'numeric',
  weekday: 'long',
}).format(new Date())

const stats = computed(() => {
  if (!overview.value) return []
  if (auth.isAdmin) {
    return [
      { label: '馆藏图书', value: overview.value.bookCount, icon: 'Collection', tone: 'teal' },
      { label: '可借库存', value: overview.value.availableCount, icon: 'Box', tone: 'green' },
      { label: '当前借出', value: overview.value.borrowedCount, icon: 'Notebook', tone: 'orange' },
      { label: '逾期记录', value: overview.value.overdueCount, icon: 'Timer', tone: 'red' },
      { label: '在册读者', value: overview.value.readerCount, icon: 'User', tone: 'blue' },
      { label: '预约中', value: overview.value.reservationCount, icon: 'AlarmClock', tone: 'teal' },
      { label: '累计归还', value: overview.value.returnedCount, icon: 'CircleCheck', tone: 'purple' },
    ]
  }
  return [
    { label: '馆藏图书', value: overview.value.bookCount, icon: 'Collection', tone: 'teal' },
    { label: '可借库存', value: overview.value.availableCount, icon: 'Box', tone: 'green' },
    { label: '我的借阅', value: overview.value.borrowedCount, icon: 'Notebook', tone: 'orange' },
    { label: '逾期记录', value: overview.value.overdueCount, icon: 'Timer', tone: 'red' },
    { label: '我的预约', value: overview.value.reservationCount, icon: 'AlarmClock', tone: 'blue' },
  ]
})

async function load() {
  loading.value = true
  try {
    overview.value = await getDashboard()
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.stat-grid {
  display: grid;
  min-height: 112px;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 14px;
}

.stat-card {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 14px;
  padding: 18px;
  border: 1px solid #e2e7e9;
  border-radius: 8px;
  background: #fff;
}

.stat-icon {
  display: grid;
  width: 42px;
  height: 42px;
  flex: 0 0 42px;
  place-items: center;
  border-radius: 8px;
  font-size: 22px;
}

.stat-content {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.stat-content span {
  color: #768187;
  font-size: 13px;
  white-space: nowrap;
}

.stat-content strong {
  margin-top: 4px;
  color: #263238;
  font-size: 25px;
  line-height: 1.1;
}

.tone-teal .stat-icon {
  color: #0f766e;
  background: #e4f3f1;
}

.tone-green .stat-icon {
  color: #25835a;
  background: #e8f5ee;
}

.tone-orange .stat-icon {
  color: #bd7b21;
  background: #fff2df;
}

.tone-red .stat-icon {
  color: #c4514e;
  background: #fdecea;
}

.tone-blue .stat-icon {
  color: #3970a7;
  background: #eaf2fb;
}

.tone-purple .stat-icon {
  color: #7662a4;
  background: #f0edf8;
}

.recent-section {
  margin-top: 30px;
}

.section-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 14px;
}

.section-heading h2 {
  margin: 0;
  color: #29353a;
  font-size: 19px;
}

.section-heading p {
  margin: 5px 0 0;
  color: #899398;
  font-size: 13px;
}

@media (max-width: 1250px) {
  .stat-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 680px) {
  .stat-grid {
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
  }

  .stat-card {
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
    padding: 15px;
  }

  .stat-content strong {
    font-size: 22px;
  }
}
</style>
