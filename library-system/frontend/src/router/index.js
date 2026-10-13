import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/store/auth'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/layout/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { title: '工作台', icon: 'DataAnalysis' },
      },
      {
        path: 'books',
        name: 'Books',
        component: () => import('@/views/BooksView.vue'),
        meta: { title: '图书管理', icon: 'Collection' },
      },
      {
        path: 'borrows',
        name: 'Borrows',
        component: () => import('@/views/BorrowRecordsView.vue'),
        meta: { title: '借阅记录', icon: 'Notebook' },
      },
      {
        path: 'reservations',
        name: 'Reservations',
        component: () => import('@/views/ReservationsView.vue'),
        meta: { title: '预约排队', icon: 'AlarmClock' },
      },
      {
        path: 'users',
        name: 'Users',
        component: () => import('@/views/UsersView.vue'),
        meta: { title: '用户管理', icon: 'User', roles: ['ADMIN'] },
      },
      {
        path: 'rules',
        name: 'BorrowRules',
        component: () => import('@/views/BorrowRulesView.vue'),
        meta: { title: '借阅规则', icon: 'SetUp', roles: ['ADMIN'] },
      },
      {
        path: 'logs',
        name: 'OperationLogs',
        component: () => import('@/views/OperationLogsView.vue'),
        meta: { title: '操作日志', icon: 'Document', roles: ['ADMIN'] },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/dashboard',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  document.title = `${to.meta.title || '工作台'} - 云阁图书借阅系统`

  if (to.path === '/login') {
    return auth.token ? '/dashboard' : true
  }
  if (!auth.token) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.meta.roles && !to.meta.roles.includes(auth.user?.role)) {
    return '/dashboard'
  }
  return true
})

export default router
