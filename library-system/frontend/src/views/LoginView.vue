<template>
  <main class="login-page">
    <section class="brand-panel">
      <div class="brand-panel-inner">
        <div class="login-brand">
          <span class="login-brand-mark">
            <el-icon><Reading /></el-icon>
          </span>
          <div>
            <strong>云阁图书借阅系统</strong>
            <small>CLOUD LIBRARY</small>
          </div>
        </div>

        <div class="brand-copy">
          <h1>让每一本书，都能被读者找到。</h1>
          <p>轻量管理馆藏、读者与借阅流转，用一套清晰的工作台完成日常图书服务。</p>
        </div>

        <div class="feature-list">
          <div>
            <el-icon><Collection /></el-icon>
            <span>馆藏信息集中管理</span>
          </div>
          <div>
            <el-icon><Notebook /></el-icon>
            <span>借还记录完整可查</span>
          </div>
          <div>
            <el-icon><Lock /></el-icon>
            <span>角色权限清晰隔离</span>
          </div>
        </div>
      </div>
    </section>

    <section class="form-panel">
      <div class="login-box">
        <div class="mobile-brand">
          <span><el-icon><Reading /></el-icon></span>
          <strong>云阁图书借阅系统</strong>
        </div>

        <h2>账号登录</h2>
        <p class="login-intro">使用管理员或读者账号进入系统</p>

        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-position="top"
          size="large"
          @keyup.enter="submit"
        >
          <el-form-item label="用户名" prop="username">
            <el-input v-model.trim="form.username" placeholder="请输入用户名" autocomplete="username">
              <template #prefix><el-icon><User /></el-icon></template>
            </el-input>
          </el-form-item>
          <el-form-item label="密码" prop="password">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="请输入密码"
              show-password
              autocomplete="current-password"
            >
              <template #prefix><el-icon><Lock /></el-icon></template>
            </el-input>
          </el-form-item>
          <el-button
            type="primary"
            size="large"
            class="login-button"
            :loading="loading"
            @click="submit"
          >
            登录
            <el-icon class="el-icon--right"><Right /></el-icon>
          </el-button>
        </el-form>

      </div>

      <footer>云阁图书借阅系统 · Spring Boot + Vue 3</footer>
    </section>
  </main>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { login } from '@/api/auth'
import { useAuthStore } from '@/store/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const formRef = ref()
const loading = ref(false)

const form = reactive({
  username: 'admin',
  password: '',
})

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function submit() {
  await formRef.value.validate()
  loading.value = true
  try {
    const data = await login(form)
    auth.setSession(data.token, data.user)
    ElMessage.success(`欢迎回来，${data.user.realName || data.user.username}`)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
    await router.replace(redirect)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  display: grid;
  min-height: 100vh;
  grid-template-columns: minmax(360px, 0.9fr) minmax(440px, 1.1fr);
  background: #f7f9fa;
}

.brand-panel {
  position: relative;
  display: flex;
  min-height: 100vh;
  align-items: center;
  overflow: hidden;
  padding: 8vw;
  color: #fff;
  background:
    linear-gradient(135deg, rgba(5, 56, 52, 0.92), rgba(15, 118, 110, 0.94)),
    #0f766e;
}

.brand-panel::after {
  position: absolute;
  right: -120px;
  bottom: -120px;
  width: 360px;
  height: 360px;
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 50%;
  box-shadow:
    0 0 0 48px rgba(255, 255, 255, 0.04),
    0 0 0 96px rgba(255, 255, 255, 0.03);
  content: "";
}

.brand-panel-inner {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 560px;
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 90px;
}

.login-brand-mark {
  display: grid;
  width: 48px;
  height: 48px;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.25);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.12);
  font-size: 27px;
}

.login-brand strong,
.login-brand small {
  display: block;
}

.login-brand strong {
  font-size: 18px;
}

.login-brand small {
  margin-top: 5px;
  color: #b9d7d3;
  font-size: 9px;
  letter-spacing: 3px;
}

.brand-copy h1 {
  max-width: 470px;
  margin: 0;
  font-family: "Songti SC", "Noto Serif CJK SC", serif;
  font-size: clamp(34px, 4.4vw, 58px);
  font-weight: 700;
  line-height: 1.22;
}

.brand-copy p {
  max-width: 480px;
  margin: 24px 0 0;
  color: #d4e9e6;
  font-size: 16px;
  line-height: 1.9;
}

.feature-list {
  display: grid;
  margin-top: 64px;
  gap: 15px;
}

.feature-list > div {
  display: flex;
  align-items: center;
  gap: 12px;
  color: #eaf6f4;
  font-size: 14px;
}

.feature-list .el-icon {
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  border-radius: 7px;
  color: #f5d294;
  background: rgba(255, 255, 255, 0.1);
  font-size: 17px;
}

.form-panel {
  display: flex;
  min-height: 100vh;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  padding: 48px 6vw;
  background: #f7f9fa;
}

.login-box {
  width: 100%;
  max-width: 430px;
}

.mobile-brand {
  display: none;
  align-items: center;
  gap: 10px;
  margin-bottom: 34px;
  color: #0f766e;
}

.mobile-brand span {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border-radius: 8px;
  color: #fff;
  background: #0f766e;
  font-size: 21px;
}

.login-box h2 {
  margin: 0;
  color: #202b30;
  font-size: 30px;
}

.login-intro {
  margin: 10px 0 34px;
  color: #7b868b;
  font-size: 14px;
}

.login-button {
  width: 100%;
  margin-top: 8px;
}

footer {
  margin-top: 40px;
  color: #a1a9ad;
  font-size: 12px;
}

@media (max-width: 900px) {
  .login-page {
    grid-template-columns: 1fr;
  }

  .brand-panel {
    display: none;
  }

  .mobile-brand {
    display: flex;
  }
}

@media (max-width: 520px) {
  .form-panel {
    justify-content: flex-start;
    padding: 64px 26px 32px;
  }
}
</style>
