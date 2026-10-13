<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1>图书管理</h1>
        <p>管理馆藏信息，维护图书库存与分类</p>
      </div>
      <el-button v-if="auth.isAdmin" type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>
        新增图书
      </el-button>
    </header>

    <div class="toolbar">
      <el-input
        v-model="query.keyword"
        clearable
        placeholder="书名、作者或 ISBN"
        style="width: 260px"
        @keyup.enter="search"
        @clear="search"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-select
        v-model="query.category"
        clearable
        filterable
        placeholder="全部分类"
        style="width: 180px"
        @change="search"
      >
        <el-option v-for="item in categories" :key="item" :label="item" :value="item" />
      </el-select>
      <el-button @click="search">
        <el-icon><RefreshLeft /></el-icon>
        查询
      </el-button>
    </div>

    <div v-loading="loading" class="table-panel">
      <el-table :data="records" style="width: 100%">
        <el-table-column label="图书" min-width="240" fixed="left">
          <template #default="{ row }">
            <div class="book-title">
              <strong>{{ row.title }}</strong>
              <span>{{ row.isbn }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="author" label="作者" min-width="150" show-overflow-tooltip />
        <el-table-column prop="publisher" label="出版社" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">{{ row.publisher || '-' }}</template>
        </el-table-column>
        <el-table-column label="分类" width="110">
          <template #default="{ row }">
            <el-tag effect="plain">{{ row.category }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="价格" width="100" align="right">
          <template #default="{ row }">¥{{ Number(row.price).toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="可借 / 馆藏" width="125" align="center">
          <template #default="{ row }">
            <span :class="{ 'stock-empty': row.availableStock === 0 }">
              {{ row.availableStock }} / {{ row.totalStock }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="updatedAt" label="更新时间" min-width="170" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <template v-if="auth.isReader">
              <template v-if="reservationOf(row)">
                <el-button
                  v-if="reservationOf(row).status === 'READY'"
                  link
                  type="success"
                  @click="handleBorrow(row)"
                >
                  取书
                </el-button>
                <el-button v-else link disabled>排队中</el-button>
              </template>
              <el-button
                v-else-if="row.availableStock > 0"
                link
                type="primary"
                @click="handleBorrow(row)"
              >
                借阅
              </el-button>
              <el-button v-else link type="warning" @click="handleReserve(row)">
                预约
              </el-button>
            </template>
            <template v-if="auth.isAdmin">
              <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
              <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无图书数据" />
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

    <el-dialog
      v-model="dialogVisible"
      :title="form.id ? '编辑图书' : '新增图书'"
      width="620px"
      destroy-on-close
      @closed="resetForm"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="92px">
        <el-row :gutter="18">
          <el-col :xs="24" :sm="12">
            <el-form-item label="ISBN" prop="isbn">
              <el-input v-model.trim="form.isbn" placeholder="请输入 ISBN" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="书名" prop="title">
              <el-input v-model.trim="form.title" placeholder="请输入书名" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="作者" prop="author">
              <el-input v-model.trim="form.author" placeholder="请输入作者" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="出版社" prop="publisher">
              <el-input v-model.trim="form.publisher" placeholder="请输入出版社" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="分类" prop="category">
              <el-select
                v-model="form.category"
                filterable
                allow-create
                default-first-option
                placeholder="选择或输入分类"
                style="width: 100%"
              >
                <el-option v-for="item in categories" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="价格" prop="price">
              <el-input-number
                v-model="form.price"
                :min="0"
                :precision="2"
                :step="1"
                controls-position="right"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="馆藏数量" prop="totalStock">
              <el-input-number
                v-model="form.totalStock"
                :min="0"
                :max="9999"
                controls-position="right"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="简介" prop="description">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="3"
            maxlength="1000"
            show-word-limit
            placeholder="图书简介"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitForm">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createBook,
  deleteBook,
  getBookCategories,
  getBooks,
  updateBook,
} from '@/api/books'
import { borrowBook } from '@/api/borrows'
import { getActiveReservations, reserveBook } from '@/api/reservations'
import { useAuthStore } from '@/store/auth'

const auth = useAuthStore()
const loading = ref(false)
const submitting = ref(false)
const dialogVisible = ref(false)
const formRef = ref()
const records = ref([])
const categories = ref([])
const reservations = ref({})
const total = ref(0)

const query = reactive({
  current: 1,
  size: 10,
  keyword: '',
  category: '',
})

const emptyForm = {
  id: null,
  isbn: '',
  title: '',
  author: '',
  publisher: '',
  category: '',
  price: 0,
  totalStock: 1,
  description: '',
}

const form = reactive({ ...emptyForm })

const rules = {
  isbn: [{ required: true, message: '请输入 ISBN', trigger: 'blur' }],
  title: [{ required: true, message: '请输入书名', trigger: 'blur' }],
  author: [{ required: true, message: '请输入作者', trigger: 'blur' }],
  category: [{ required: true, message: '请选择或输入分类', trigger: 'change' }],
  price: [{ required: true, message: '请输入价格', trigger: 'change' }],
  totalStock: [{ required: true, message: '请输入馆藏数量', trigger: 'change' }],
}

function search() {
  query.current = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const data = await getBooks({
      current: query.current,
      size: query.size,
      keyword: query.keyword || undefined,
      category: query.category || undefined,
    })
    records.value = data.records
    total.value = data.total
    await loadReservations()
  } finally {
    loading.value = false
  }
}

async function loadReservations() {
  if (!auth.isReader) {
    reservations.value = {}
    return
  }
  const list = await getActiveReservations()
  reservations.value = Object.fromEntries(list.map((item) => [item.bookId, item]))
}

function reservationOf(row) {
  return reservations.value[row.id]
}

async function loadCategories() {
  categories.value = await getBookCategories()
}

function resetForm() {
  Object.assign(form, emptyForm)
  formRef.value?.clearValidate()
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

function openEdit(row) {
  Object.assign(form, {
    id: row.id,
    isbn: row.isbn,
    title: row.title,
    author: row.author,
    publisher: row.publisher || '',
    category: row.category,
    price: Number(row.price),
    totalStock: row.totalStock,
    description: row.description || '',
  })
  dialogVisible.value = true
}

async function submitForm() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    if (form.id) {
      await updateBook(form.id, { ...form })
      ElMessage.success('图书信息已更新')
    } else {
      await createBook({ ...form })
      ElMessage.success('图书已添加')
    }
    dialogVisible.value = false
    await Promise.all([load(), loadCategories()])
  } finally {
    submitting.value = false
  }
}

async function handleBorrow(row) {
  await ElMessageBox.confirm(
    `确认借阅《${row.title}》吗？借期为 30 天。`,
    '借阅确认',
    {
      confirmButtonText: '确认借阅',
      cancelButtonText: '取消',
      type: 'info',
    },
  )
  await borrowBook(row.id)
  ElMessage.success('借阅成功，请按时归还')
  await load()
}

async function handleReserve(row) {
  await ElMessageBox.confirm(
    `《${row.title}》当前无可借库存，确认加入预约队列吗？到书后请在保留期内取书。`,
    '预约确认',
    {
      confirmButtonText: '确认预约',
      cancelButtonText: '取消',
      type: 'info',
    },
  )
  await reserveBook(row.id)
  ElMessage.success('预约成功，请留意到书通知')
  await load()
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确认删除《${row.title}》吗？此操作不可撤销。`, '删除图书', {
    confirmButtonText: '确认删除',
    cancelButtonText: '取消',
    type: 'warning',
  })
  await deleteBook(row.id)
  ElMessage.success('图书已删除')
  if (records.value.length === 1 && query.current > 1) query.current -= 1
  await Promise.all([load(), loadCategories()])
}

onMounted(() => Promise.all([load(), loadCategories()]))
</script>

<style scoped>
.stock-empty {
  color: #d14743;
  font-weight: 600;
}
</style>
