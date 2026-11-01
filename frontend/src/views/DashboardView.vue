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

    <section v-if="auth.isAdmin" class="chart-section">
      <div class="section-heading">
        <div>
          <h2>借阅分析</h2>
          <p>快速了解热门图书与馆藏分类结构</p>
        </div>
      </div>

      <div class="chart-grid">
        <article class="chart-panel">
          <header class="chart-header">
            <div>
              <h3>图书借阅情况</h3>
              <p>{{ barView === 'books' ? '借阅次数最多的 10 本图书' : '各分类图书的累计借阅次数' }}</p>
            </div>
            <el-radio-group v-model="barView" size="small">
              <el-radio-button value="books">借阅排行</el-radio-button>
              <el-radio-button value="categories">分类对比</el-radio-button>
            </el-radio-group>
          </header>
          <div v-show="topBooks.length || borrowCategories.length" ref="barChartRef" class="chart-canvas"></div>
          <el-empty
            v-if="!topBooks.length && !borrowCategories.length"
            description="暂无借阅数据"
            :image-size="72"
          />
        </article>

        <article class="chart-panel">
          <header class="chart-header">
            <div>
              <h3>图书分类占比</h3>
              <p>全馆馆藏册数按图书分类的分布</p>
            </div>
          </header>
          <div v-show="categoryDistribution.length" ref="pieChartRef" class="chart-canvas"></div>
          <el-empty
            v-if="!categoryDistribution.length"
            description="暂无分类数据"
            :image-size="72"
          />
        </article>
      </div>
    </section>

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
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { BarChart, PieChart } from 'echarts/charts'
import { GraphicComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { getDashboard } from '@/api/dashboard'
import { useAuthStore } from '@/store/auth'

echarts.use([
  BarChart,
  PieChart,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
])

const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)
const overview = ref(null)
const barView = ref('books')
const barChartRef = ref(null)
const pieChartRef = ref(null)

let barChart = null
let pieChart = null
let resizeObserver = null

const topBooks = computed(() => overview.value?.topBorrowedBooks || [])
const borrowCategories = computed(() => overview.value?.borrowCategoryStats || [])
const categoryDistribution = computed(() => overview.value?.categoryDistribution || [])

const CHART_COLORS = [
  '#0f766e',
  '#2f7d95',
  '#3d7ea6',
  '#6c8ebf',
  '#7662a4',
  '#a05fa8',
  '#c05f86',
  '#c4514e',
  '#d17c3f',
  '#c8872a',
  '#a99a3c',
  '#6b9e4f',
  '#25835a',
  '#2a9d8f',
  '#5e7f7b',
  '#8b7d6b',
  '#9a6b52',
  '#4c7c8a',
]

const formatCount = (value) => Number(value || 0).toLocaleString('zh-CN')

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
    await nextTick()
    renderCharts()
  } finally {
    loading.value = false
  }
}

function initCharts() {
  if (!auth.isAdmin || !barChartRef.value || !pieChartRef.value) return
  barChart = echarts.init(barChartRef.value)
  pieChart = echarts.init(pieChartRef.value)
  resizeObserver = new ResizeObserver(() => {
    barChart?.resize()
    pieChart?.resize()
  })
  resizeObserver.observe(barChartRef.value)
  resizeObserver.observe(pieChartRef.value)
}

function renderBarChart() {
  if (!barChart) return

  const booksMode = barView.value === 'books'
  const raw = booksMode ? topBooks.value : borrowCategories.value
  const items = [...raw]
  if (booksMode) items.reverse()

  barChart.setOption(
    {
      animationDuration: 500,
      color: [booksMode ? '#0f766e' : '#3d7ea6'],
      grid: { top: 18, right: 24, bottom: 12, left: 16, containLabel: true },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        confine: true,
        formatter: (params) => {
          const item = params[0]
          const label = booksMode ? '借阅次数' : '累计借阅'
          return `${item.name}<br/>${label}：${formatCount(item.value)} 次`
        },
      },
      xAxis: {
        type: 'value',
        minInterval: 1,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: '#899398' },
        splitLine: { lineStyle: { color: '#edf1f3' } },
      },
      yAxis: {
        type: 'category',
        data: items.map((item) => item.name),
        axisLine: { lineStyle: { color: '#dde3e6' } },
        axisTick: { show: false },
        axisLabel: {
          color: '#4f595f',
          width: 96,
          overflow: 'truncate',
        },
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 20,
          data: items.map((item) => item.value),
          itemStyle: { borderRadius: [0, 4, 4, 0] },
          label: {
            show: true,
            position: 'right',
            color: '#4f595f',
            formatter: ({ value }) => formatCount(value),
          },
        },
      ],
    },
    true,
  )
}

function renderPieChart() {
  if (!pieChart) return

  const items = categoryDistribution.value
  const total = items.reduce((sum, item) => sum + Number(item.value || 0), 0)

  pieChart.setOption(
    {
      animationDuration: 500,
      color: CHART_COLORS,
      tooltip: {
        trigger: 'item',
        confine: true,
        formatter: ({ marker, name, value, percent, data }) => {
          const titleCount = data?.titleCount
          const lines = [
            `${marker}${name}`,
            `馆藏册数：${formatCount(value)} 册`,
            `品种数：${formatCount(titleCount)} 种`,
            `占比：${percent}%`,
          ]
          return lines.join('<br/>')
        },
      },
      legend: {
        type: 'scroll',
        bottom: 0,
        left: 'center',
        icon: 'circle',
        itemWidth: 8,
        itemHeight: 8,
        itemGap: 12,
        textStyle: { color: '#5f6a70', fontSize: 12 },
      },
      graphic: [
        {
          type: 'text',
          left: 'center',
          top: '34%',
          style: {
            text: '馆藏册数',
            fill: '#899398',
            fontSize: 12,
            textAlign: 'center',
          },
        },
        {
          type: 'text',
          left: 'center',
          top: '40%',
          style: {
            text: formatCount(total),
            fill: '#263238',
            fontSize: 24,
            fontWeight: 700,
            textAlign: 'center',
          },
        },
      ],
      series: [
        {
          name: '图书分类占比',
          type: 'pie',
          radius: ['48%', '68%'],
          center: ['50%', '42%'],
          avoidLabelOverlap: true,
          itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 4 },
          label: { show: false },
          labelLine: { show: false },
          emphasis: {
            scaleSize: 6,
            label: {
              show: true,
              fontSize: 13,
              fontWeight: 600,
              formatter: '{b}\n{d}%',
            },
          },
          data: items.map((item) => ({
            name: item.name,
            value: item.value,
            titleCount: item.titleCount,
          })),
        },
      ],
    },
    true,
  )
}

function renderCharts() {
  if (!auth.isAdmin) return
  if (barChartRef.value && pieChartRef.value && (!barChart || !pieChart)) {
    initCharts()
  }
  if (!barChart || !pieChart) return
  if (topBooks.value.length || borrowCategories.value.length) {
    renderBarChart()
  } else {
    barChart.clear()
  }
  if (categoryDistribution.value.length) {
    renderPieChart()
  } else {
    pieChart.clear()
  }
}

watch(barView, () => {
  renderBarChart()
})

onMounted(async () => {
  await load()
  await nextTick()
  renderCharts()
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  barChart?.dispose()
  pieChart?.dispose()
  barChart = null
  pieChart = null
})
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

.chart-section {
  margin-top: 30px;
}

.chart-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr);
  gap: 14px;
}

.chart-panel {
  min-width: 0;
  padding: 18px 18px 10px;
  border: 1px solid #e2e7e9;
  border-radius: 8px;
  background: #fff;
}

.chart-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.chart-header h3 {
  margin: 0;
  color: #29353a;
  font-size: 16px;
}

.chart-header p {
  margin: 5px 0 0;
  color: #899398;
  font-size: 13px;
}

.chart-canvas {
  width: 100%;
  height: 360px;
  margin-top: 8px;
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

  .chart-grid {
    grid-template-columns: 1fr;
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

  .chart-header {
    flex-direction: column;
  }

  .chart-canvas {
    height: 300px;
  }
}
</style>
