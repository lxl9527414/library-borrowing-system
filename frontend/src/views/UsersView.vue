<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1>用户管理</h1>
        <p>维护管理员与普通读者账号</p>
      </div>
      <el-button type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>
        新增用户
      </el-button>
    </header>

    <div class="toolbar">
      <el-input
        v-model="query.keyword"
        clearable
        placeholder="用户名或姓名"
        style="width: 260px"
        @keyup.enter="search"
        @clear="search"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-button @click="search">
        <el-icon><RefreshLeft /></el-icon>
        查询
      </el-button>
    </div>

    <div v-loading="loading" class="table-panel">
      <el-table :data="records" style="width: 100%">
        <el-table-column prop="username" label="用户名" min-width="150" fixed="left">
          <template #default="{ row }">
            <div class="user-cell">
              <strong>{{ row.username }}</strong>
              <el-tag v-if="row.id === auth.user?.id" size="small" effect="plain">当前账号</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="realName" label="姓名" min-width="130">
          <template #default="{ row }">{{ row.realName || '-' }}</template>
        </el-table-column>
        <el-table-column label="角色" width="110">
          <template #default="{ row }">
            <el-tag :type="row.role === 'ADMIN' ? 'warning' : 'success'" effect="light">
              {{ row.role === 'ADMIN' ? '管理员' : '读者' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-switch
              v-model="row.status"
              :active-value="1"
              :inactive-value="0"
              :disabled="row.id === auth.user?.id"
              @change="handleStatusChange(row)"
            />
          </template>
        </el-table-column>
        <el-table-column prop="createdAt" label="创建时间" min-width="170" />
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" @click="handleResetPassword(row)">重置密码</el-button>
            <el-button link type="danger" :disabled="row.id === auth.user?.id" @click="handleDelete(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
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
      :title="form.id ? '编辑用户' : '新增用户'"
      width="520px"
      destroy-on-close
      @closed="resetForm"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="86px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model.trim="form.username" placeholder="请输入用户名" />
        </el-form-item>
        <el-form-item label="姓名" prop="realName">
          <el-input v-model.trim="form.realName" placeholder="请输入姓名" />
        </el-form-item>
        <el-form-item label="角色" prop="role">
          <el-radio-group v-model="form.role">
            <el-radio-button value="READER">普通读者</el-radio-button>
            <el-radio-button value="ADMIN">管理员</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="form.id ? '新密码' : '初始密码'" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="6-30 位密码"
          />
        </el-form-item>
        <el-form-item v-if="form.id" label="状态" prop="status">
          <el-switch v-model="form.status" :active-value="1" :inactive-value="0" />
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
  createUser,
  deleteUser,
  getUsers,
  resetUserPassword,
  updateUser,
} from '@/api/users'
import { useAuthStore } from '@/store/auth'

const auth = useAuthStore()
const loading = ref(false)
const submitting = ref(false)
const dialogVisible = ref(false)
const formRef = ref()
const records = ref([])
const total = ref(0)
const query = reactive({
  current: 1,
  size: 10,
  keyword: '',
})

const emptyForm = {
  id: null,
  username: '',
  realName: '',
  role: 'READER',
  status: 1,
  password: '',
}

const form = reactive({ ...emptyForm })

const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 50, message: '用户名长度应为 3-50 位', trigger: 'blur' },
  ],
  role: [{ required: true, message: '请选择角色', trigger: 'change' }],
  password: [
    {
      validator: (_, value, callback) => {
        if (!form.id && !value) return callback()
        if (value && (value.length < 6 || value.length > 30)) {
          return callback(new Error('密码长度应为 6-30 位'))
        }
        callback()
      },
      trigger: 'blur',
    },
  ],
}

function search() {
  query.current = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const data = await getUsers({
      current: query.current,
      size: query.size,
      keyword: query.keyword || undefined,
    })
    records.value = data.records
    total.value = data.total
  } finally {
    loading.value = false
  }
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
    username: row.username,
    realName: row.realName || '',
    role: row.role,
    status: row.status,
    password: '',
  })
  dialogVisible.value = true
}

async function submitForm() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    const payload = { ...form, password: form.password || undefined }
    if (form.id) {
      await updateUser(form.id, payload)
      ElMessage.success('用户信息已更新')
    } else {
      await createUser(payload)
      ElMessage.success('用户已创建')
    }
    dialogVisible.value = false
    await load()
  } finally {
    submitting.value = false
  }
}

async function handleStatusChange(row) {
  try {
    await updateUser(row.id, {
      username: row.username,
      realName: row.realName,
      role: row.role,
      status: row.status,
    })
    ElMessage.success(row.status === 1 ? '账号已启用' : '账号已停用')
  } catch {
    row.status = row.status === 1 ? 0 : 1
  }
}

async function handleResetPassword(row) {
  const { value } = await ElMessageBox.prompt(
    `为 ${row.username} 设置新密码`,
    '重置密码',
    {
      confirmButtonText: '确认重置',
      cancelButtonText: '取消',
      inputType: 'password',
      inputPlaceholder: '6-30 位新密码',
      inputValidator: (input) =>
        input && input.length >= 6 && input.length <= 30 ? true : '密码长度应为 6-30 位',
    },
  )
  await resetUserPassword(row.id, value)
  ElMessage.success('密码已重置')
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确认删除用户「${row.username}」吗？`, '删除用户', {
    confirmButtonText: '确认删除',
    cancelButtonText: '取消',
    type: 'warning',
  })
  await deleteUser(row.id)
  ElMessage.success('用户已删除')
  if (records.value.length === 1 && query.current > 1) query.current -= 1
  await load()
}

onMounted(load)
</script>

<style scoped>
.user-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>

