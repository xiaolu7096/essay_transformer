// T009：四条基础路由一次性注册；视图用动态 import，随各故事逐个创建。
// T033：追加 beforeEach 全局守卫（不改路由表）。
import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes = [
  {
    path: '/',
    name: 'landing',
    component: () => import('../views/LandingView.vue'),
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { guestOnly: true },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('../views/RegisterView.vue'),
    meta: { guestOnly: true },
  },
  {
    path: '/home',
    name: 'home',
    component: () => import('../views/HomeView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/credentials',
    name: 'credentials',
    component: () => import('../views/CredentialsView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/tasks/new',
    name: 'task-create',
    component: () => import('../views/TaskCreateView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/tasks',
    name: 'task-list',
    component: () => import('../views/TaskListView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/tasks/:id',
    name: 'task-detail',
    component: () => import('../views/TaskDetailView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/tasks/:id/reader',
    name: 'task-reader',
    component: () => import('../views/ReaderView.vue'),
    meta: { requiresAuth: true },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// T033：匿名访问受保护路由 → 登录页并携带 redirect，登录后回跳（FR-010）
// 已登录用户访问登录/注册页 → 直接进应用，避免重复登录
router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.guestOnly && auth.isAuthenticated) {
    return { name: 'home' }
  }
  return true
})

export default router
