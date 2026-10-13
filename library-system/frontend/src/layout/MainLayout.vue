<template>
  <div
    class="app-shell"
    :class="{
      'is-collapsed': collapsed && !isMobile,
      'is-menu-open': isMobile && menuOpen,
    }"
  >
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">
          <el-icon><Reading /></el-icon>
        </span>
        <span v-show="!collapsed || isMobile" class="brand-copy">
          <strong>云阁图书</strong>
          <small>LIBRARY</small>
        </span>
      </div>

      <el-menu
        :default-active="route.path"
        :collapse="collapsed && !isMobile"
        :collapse-transition="false"
        router
        class="nav-menu"
      >
        <el-menu-item v-for="item in menuItems" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <template #title>{{ item.title }}</template>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-foot">
        <span v-if="!collapsed || isMobile" class="status-dot"></span>
        <span v-show="!collapsed || isMobile">服务运行中</span>
      </div>
    </aside>

    <div v-if="isMobile && menuOpen" class="menu-mask" @click="menuOpen = false"></div>

    <section class="workspace">
      <header class="topbar">
        <div class="topbar-title">
          <el-tooltip :content="isMobile ? '菜单' : collapsed ? '展开菜单' : '收起菜单'" placement="bottom">
            <el-button text circle class="menu-button" @click="toggleMenu">
              <el-icon><Expand v-if="collapsed && !isMobile" /><Fold v-else-if="!isMobile" /><Menu v-else /></el-icon>
            </el-button>
          </el-tooltip>
          <div>
            <h2>{{ route.meta.title }}</h2>
            <p>欢迎回来，{{ auth.user?.realName || auth.user?.username }}</p>
          </div>
        </div>

        <div class="account">
          <el-tag :type="auth.isAdmin ? 'warning' : 'success'" effect="light" round>
            {{ auth.isAdmin ? '管理员' : '读者' }}
          </el-tag>
          <el-dropdown trigger="click" @command="handleCommand">
            <button class="account-button" type="button">
              <span class="avatar">{{ avatarText }}</span>
              <span class="account-name">{{ auth.user?.realName || auth.user?.username }}</span>
              <el-icon><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item disabled>
                  {{ auth.user?.username }}
                </el-dropdown-item>
                <el-dropdown-item divided command="logout">
                  <el-icon><SwitchButton /></el-icon>
                  退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <main class="content">
        <router-view v-slot="{ Component }">
          <transition name="page-fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </main>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/store/auth'
import { logout as logoutApi } from '@/api/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const collapsed = ref(false)
const menuOpen = ref(false)
const isMobile = ref(false)

const allItems = [
  { path: '/dashboard', title: '工作台', icon: 'DataAnalysis' },
  { path: '/books', title: '图书管理', icon: 'Collection' },
  { path: '/borrows', title: '借阅记录', icon: 'Notebook' },
  { path: '/reservations', title: '预约排队', icon: 'AlarmClock' },
  { path: '/users', title: '用户管理', icon: 'User', roles: ['ADMIN'] },
  { path: '/rules', title: '借阅规则', icon: 'SetUp', roles: ['ADMIN'] },
  { path: '/logs', title: '操作日志', icon: 'Document', roles: ['ADMIN'] },
]

const menuItems = computed(() =>
  allItems.filter((item) => !item.roles || item.roles.includes(auth.user?.role)),
)

const avatarText = computed(() => {
  const name = auth.user?.realName || auth.user?.username || 'U'
  return name.slice(0, 1).toUpperCase()
})

function updateViewport() {
  isMobile.value = window.innerWidth < 800
  if (!isMobile.value) {
    menuOpen.value = false
  }
}

function toggleMenu() {
  if (isMobile.value) {
    menuOpen.value = !menuOpen.value
  } else {
    collapsed.value = !collapsed.value
  }
}

async function handleCommand(command) {
  if (command !== 'logout') return
  try {
    await logoutApi()
  } finally {
    auth.logout()
    ElMessage.success('已退出登录')
    router.replace('/login')
  }
}

watch(
  () => route.path,
  () => {
    if (isMobile.value) menuOpen.value = false
  },
)

onMounted(() => {
  updateViewport()
  window.addEventListener('resize', updateViewport)
})

onBeforeUnmount(() => window.removeEventListener('resize', updateViewport))
</script>

<style scoped>
.app-shell {
  min-height: 100vh;
  background: #f4f6f8;
}

.sidebar {
  position: fixed;
  z-index: 20;
  top: 0;
  bottom: 0;
  left: 0;
  display: flex;
  width: 228px;
  flex-direction: column;
  border-right: 1px solid #e0e5e8;
  background: #fbfcfc;
  transition: width 0.2s ease, transform 0.2s ease;
}

.is-collapsed .sidebar {
  width: 72px;
}

.brand {
  display: flex;
  height: 72px;
  align-items: center;
  gap: 12px;
  padding: 0 18px;
  border-bottom: 1px solid #e8ecee;
}

.brand-mark {
  display: grid;
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  place-items: center;
  border-radius: 8px;
  color: #fff;
  background: #0f766e;
  font-size: 22px;
}

.brand-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  line-height: 1.1;
}

.brand-copy strong {
  color: #213237;
  font-size: 17px;
  white-space: nowrap;
}

.brand-copy small {
  margin-top: 5px;
  color: #7a858a;
  font-size: 9px;
  letter-spacing: 2px;
  white-space: nowrap;
}

.nav-menu {
  flex: 1;
  padding: 14px 10px;
  border-right: 0;
  background: transparent;
}

.nav-menu :deep(.el-menu-item) {
  height: 46px;
  margin: 4px 0;
  border-radius: 7px;
  color: #4c585d;
  font-weight: 500;
}

.nav-menu :deep(.el-menu-item:hover) {
  background: #eef5f4;
}

.nav-menu :deep(.el-menu-item.is-active) {
  color: #0f766e;
  background: #e6f2f0;
}

.nav-menu :deep(.el-menu-item .el-icon) {
  font-size: 19px;
}

.sidebar-foot {
  display: flex;
  height: 58px;
  align-items: center;
  gap: 8px;
  padding: 0 24px;
  border-top: 1px solid #e8ecee;
  color: #7b868b;
  font-size: 12px;
  white-space: nowrap;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #22a06b;
  box-shadow: 0 0 0 4px #e3f5ec;
}

.workspace {
  min-height: 100vh;
  margin-left: 228px;
  transition: margin-left 0.2s ease;
}

.is-collapsed .workspace {
  margin-left: 72px;
}

.topbar {
  position: sticky;
  z-index: 10;
  top: 0;
  display: flex;
  height: 72px;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 0 28px;
  border-bottom: 1px solid #e4e8eb;
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(10px);
}

.topbar-title {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
}

.topbar-title h2 {
  margin: 0;
  color: #263137;
  font-size: 18px;
  line-height: 1.2;
}

.topbar-title p {
  margin: 4px 0 0;
  color: #8a9398;
  font-size: 12px;
}

.menu-button {
  width: 38px;
  height: 38px;
  color: #576268;
  font-size: 19px;
}

.account {
  display: flex;
  align-items: center;
  gap: 12px;
}

.account-button {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 4px;
  border: 0;
  outline: 0;
  color: #364248;
  background: transparent;
  cursor: pointer;
}

.avatar {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  background: #c8872a;
  font-size: 15px;
  font-weight: 700;
}

.account-name {
  max-width: 120px;
  overflow: hidden;
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.content {
  padding: 26px 28px 36px;
}

.page-fade-enter-active,
.page-fade-leave-active {
  transition: opacity 0.16s ease;
}

.page-fade-enter-from,
.page-fade-leave-to {
  opacity: 0;
}

.menu-mask {
  position: fixed;
  z-index: 15;
  inset: 0;
  background: rgba(20, 28, 31, 0.38);
}

@media (max-width: 800px) {
  .sidebar {
    width: min(82vw, 260px);
    transform: translateX(-100%);
  }

  .is-menu-open .sidebar {
    transform: translateX(0);
    box-shadow: 18px 0 50px rgba(23, 34, 38, 0.18);
  }

  .workspace,
  .is-collapsed .workspace {
    margin-left: 0;
  }

  .topbar {
    height: 64px;
    padding: 0 14px;
  }

  .topbar-title p,
  .account-name,
  .account > .el-tag {
    display: none;
  }

  .content {
    padding: 20px 14px 28px;
  }
}
</style>
