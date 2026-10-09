<template>
  <div class="app-layout">
    <!-- 移动端抽屉遮罩 -->
    <div
      v-if="drawerOpen && isMobile"
      class="drawer-backdrop"
      @click="drawerOpen = false"
    />

    <header class="app-header">
      <div class="app-brand" @click="goHome">
        <BrandLogo />
        <span class="app-brand-text">纸慧</span>
      </div>

      <!-- 桌面端导航 -->
      <nav class="app-nav desktop-nav">
        <router-link to="/tasks" class="app-nav-link">
          <span class="nav-dot"></span>
          任务列表
        </router-link>
        <router-link to="/tasks/new" class="app-nav-link">
          <span class="nav-dot"></span>
          提交任务
        </router-link>
        <router-link to="/credentials" class="app-nav-link">
          <span class="nav-dot"></span>
          密钥设置
        </router-link>
      </nav>

      <div class="app-user">
        <!-- 移动端菜单按钮 -->
        <button
          v-if="isMobile"
          class="menu-btn"
          @click="toggleDrawer"
          aria-label="打开菜单"
        >
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M3 5h14M3 10h14M3 15h14" stroke-linecap="round" />
          </svg>
        </button>

        <button
          class="theme-btn"
          @click="toggleTheme"
          :title="theme === 'dark' ? '切换到亮色主题' : '切换到暗色主题'"
        >
          <svg v-if="theme === 'dark'" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5">
            <circle cx="10" cy="10" r="3.5" />
            <path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.5 4.5l1.5 1.5M14 14l1.5 1.5M4.5 15.5L6 14M14 6l1.5-1.5" stroke-linecap="round" />
          </svg>
          <svg v-else viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M15 13A6 6 0 017 5a7 7 0 108 8z" stroke-linejoin="round" />
          </svg>
        </button>

        <!-- 桌面端用户下拉 -->
        <div v-if="!isMobile" class="user-dropdown" :class="{ open: userMenuOpen }">
          <button class="user-trigger" @click="toggleUserMenu">
            <div class="user-avatar">
              {{ (username || '?').charAt(0).toUpperCase() }}
            </div>
            <span class="user-name" data-testid="current-username">{{ username || '…' }}</span>
            <svg class="user-chevron" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M3 4.5l3 3 3-3" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </button>

          <div v-if="userMenuOpen" class="user-menu" @click.stop>
            <div class="user-menu-header">
              <div class="user-menu-avatar">
                {{ (username || '?').charAt(0).toUpperCase() }}
              </div>
              <div class="user-menu-info">
                <div class="user-menu-name">{{ username }}</div>
                <div class="user-menu-email">{{ username }}@example.com</div>
              </div>
            </div>
            <div class="user-menu-divider"></div>
            <div class="user-menu-section">
              <button class="user-menu-item" @click="goTasks">
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                  <rect x="2.5" y="3.5" width="11" height="9" rx="1.5" />
                  <path d="M5 6h6M5 8.5h6M5 11h4" stroke-linecap="round" />
                </svg>
                我的任务
              </button>
              <button class="user-menu-item" @click="goCredentials">
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                  <rect x="3" y="5" width="10" height="8" rx="1.5" />
                  <path d="M5 5V3.5a3 3 0 016 0V5" stroke-linecap="round" />
                  <circle cx="8" cy="9" r="1" />
                </svg>
                密钥管理
              </button>
            </div>
            <div class="user-menu-divider"></div>
            <div class="user-menu-section">
              <button class="user-menu-item" disabled>
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                  <circle cx="8" cy="8" r="6.5" />
                  <path d="M6 6a2 2 0 013.5-1M8 9v2" stroke-linecap="round" />
                </svg>
                帮助文档
                <span class="menu-soon">敬请期待</span>
              </button>
            </div>
            <div class="user-menu-divider"></div>
            <div class="user-menu-section">
              <button
                class="user-menu-item danger"
                data-testid="logout-button"
                @click="onLogout"
              >
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M6 3.5H4a1.5 1.5 0 00-1.5 1.5v6A1.5 1.5 0 004 12.5h2M10 5.5l2.5 2.5L10 10.5M12.5 8H5.5" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                退出登录
              </button>
            </div>
          </div>
        </div>
      </div>
    </header>

    <!-- 移动端抽屉 -->
    <aside v-if="isMobile" class="mobile-drawer" :class="{ open: drawerOpen }">
      <div class="drawer-header">
        <div class="drawer-user">
          <div class="drawer-avatar">
            {{ (username || '?').charAt(0).toUpperCase() }}
          </div>
          <div class="drawer-user-info">
            <div class="drawer-username">{{ username }}</div>
            <div class="drawer-user-email">{{ username }}@example.com</div>
          </div>
        </div>
        <button class="drawer-close" @click="drawerOpen = false" aria-label="关闭菜单">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M3 3l10 10M13 3L3 13" stroke-linecap="round" />
          </svg>
        </button>
      </div>

      <nav class="drawer-nav">
        <div class="drawer-nav-section">
          <div class="drawer-nav-label">导航</div>
          <router-link
            to="/tasks"
            class="drawer-nav-item"
            @click="drawerOpen = false"
          >
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
              <rect x="2.5" y="3.5" width="11" height="9" rx="1.5" />
              <path d="M5 6h6M5 8.5h6M5 11h4" stroke-linecap="round" />
            </svg>
            任务列表
            <span v-if="$route.path === '/tasks'" class="drawer-nav-dot"></span>
          </router-link>
          <router-link
            to="/tasks/new"
            class="drawer-nav-item"
            @click="drawerOpen = false"
          >
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M8 3v10M3 8h10" stroke-linecap="round" />
            </svg>
            提交任务
            <span v-if="$route.path === '/tasks/new'" class="drawer-nav-dot"></span>
          </router-link>
          <router-link
            to="/credentials"
            class="drawer-nav-item"
            @click="drawerOpen = false"
          >
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
              <rect x="3" y="5" width="10" height="8" rx="1.5" />
              <path d="M5 5V3.5a3 3 0 016 0V5" stroke-linecap="round" />
              <circle cx="8" cy="9" r="1" />
            </svg>
            密钥设置
            <span v-if="$route.path === '/credentials'" class="drawer-nav-dot"></span>
          </router-link>
        </div>

        <div class="drawer-nav-section">
          <div class="drawer-nav-label">更多</div>
          <button class="drawer-nav-item" disabled>
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
              <circle cx="8" cy="8" r="6.5" />
              <path d="M6 6a2 2 0 013.5-1M8 9v2" stroke-linecap="round" />
            </svg>
            帮助文档
            <span class="menu-soon">敬请期待</span>
          </button>
        </div>
      </nav>

      <div class="drawer-footer">
        <button class="drawer-logout" data-testid="logout-button" @click="onLogout">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M6 3.5H4a1.5 1.5 0 00-1.5 1.5v6A1.5 1.5 0 004 12.5h2M10 5.5l2.5 2.5L10 10.5M12.5 8H5.5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          退出登录
        </button>
      </div>
    </aside>

    <main class="app-content">
      <slot />
    </main>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useTheme } from '../composables/useTheme'
import BrandLogo from '../components/BrandLogo.vue'

const props = defineProps({
  username: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['logout'])
const router = useRouter()
const { theme, toggleTheme } = useTheme()

const userMenuOpen = ref(false)
const drawerOpen = ref(false)
const isMobile = ref(false)

function checkMobile() {
  isMobile.value = window.innerWidth < 768
}

function toggleUserMenu(e) {
  e.stopPropagation()
  userMenuOpen.value = !userMenuOpen.value
}

function closeUserMenu() {
  userMenuOpen.value = false
}

function toggleDrawer() {
  drawerOpen.value = !drawerOpen.value
}

function goHome() {
  router.push('/tasks')
}

function goTasks() {
  router.push('/tasks')
  userMenuOpen.value = false
  drawerOpen.value = false
}

function goCredentials() {
  router.push('/credentials')
  userMenuOpen.value = false
  drawerOpen.value = false
}

function onLogout() {
  userMenuOpen.value = false
  drawerOpen.value = false
  emit('logout')
}

onMounted(() => {
  checkMobile()
  window.addEventListener('resize', checkMobile)
  document.addEventListener('click', closeUserMenu)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', checkMobile)
  document.removeEventListener('click', closeUserMenu)
})
</script>

<style scoped>
.app-layout {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary);
}

.app-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  padding: 0 var(--spacing-6);
  height: 56px;
  border-bottom: 1px solid var(--border-primary);
  background: var(--bg-primary);
  position: sticky;
  top: 0;
  z-index: var(--z-sticky);
  box-shadow: var(--shadow-sm);
  backdrop-filter: blur(8px);
}

.app-brand {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
  font-weight: var(--font-weight-semibold);
  font-size: var(--font-size-md);
  color: var(--text-primary);
  cursor: pointer;
  user-select: none;
  transition: opacity var(--transition-fast);
}

.app-brand:hover {
  opacity: 0.8;
}

.app-logo {
  width: 28px;
  height: 28px;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  justify-content: center;
}

.app-logo svg {
  width: 28px;
  height: 28px;
}

.app-brand-text {
  letter-spacing: 0.2px;
}

.app-nav {
  flex: 1;
  display: flex;
  gap: var(--spacing-1);
  margin-left: var(--spacing-8);
}

.desktop-nav {
  display: flex;
}

.app-nav-link {
  position: relative;
  color: var(--text-tertiary);
  text-decoration: none;
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-medium);
  padding: var(--spacing-2) var(--spacing-3);
  border-radius: var(--radius-md);
  transition: color var(--transition-fast), background-color var(--transition-fast);
  display: flex;
  align-items: center;
  gap: 6px;
}

.app-nav-link:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
  text-decoration: none;
}

.nav-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--text-quaternary);
  opacity: 0;
  transition: all var(--transition-fast);
}

.app-nav-link:hover .nav-dot {
  opacity: 0.5;
}

.app-nav-link.router-link-active {
  color: var(--text-primary);
  font-weight: var(--font-weight-semibold);
  background: var(--bg-tertiary);
}

.app-nav-link.router-link-active .nav-dot {
  opacity: 1;
  background: var(--text-primary);
  width: 6px;
  height: 6px;
}

.app-user {
  display: flex;
  align-items: center;
  gap: var(--spacing-3);
  margin-left: auto;
}

/* 移动端菜单按钮 */
.menu-btn {
  width: 36px;
  height: 36px;
  border: 1px solid var(--border-primary);
  background: var(--bg-primary);
  color: var(--text-secondary);
  border-radius: var(--radius-md);
  cursor: pointer;
  display: none;
  align-items: center;
  justify-content: center;
  transition: all var(--transition-fast);
}

.menu-btn svg {
  width: 18px;
  height: 18px;
}

.menu-btn:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
  border-color: var(--border-strong);
}

.theme-btn {
  width: 32px;
  height: 32px;
  border: 1px solid var(--border-primary);
  background: var(--bg-primary);
  color: var(--text-secondary);
  border-radius: var(--radius-md);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--transition-fast);
}

.theme-btn svg {
  width: 16px;
  height: 16px;
}

.theme-btn:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
  border-color: var(--border-strong);
}

.user-dropdown {
  position: relative;
}

.user-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 8px 3px 3px;
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-full);
  background: var(--bg-primary);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.user-trigger:hover {
  border-color: var(--border-strong);
  background: var(--bg-hover);
}

.user-avatar {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: var(--bg-tertiary);
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  text-transform: uppercase;
}

.user-name {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  font-weight: var(--font-weight-medium);
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-chevron {
  width: 12px;
  height: 12px;
  color: var(--text-tertiary);
  transition: transform var(--transition-fast);
}

.user-dropdown.open .user-chevron {
  transform: rotate(180deg);
}

.user-menu {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  width: 240px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: var(--spacing-2);
  z-index: var(--z-dropdown);
  animation: menuFadeIn var(--transition-normal) ease;
}

@keyframes menuFadeIn {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.user-menu-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: var(--spacing-2) var(--spacing-2) var(--spacing-3);
}

.user-menu-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--bg-tertiary);
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  text-transform: uppercase;
  flex-shrink: 0;
}

.user-menu-info {
  min-width: 0;
  flex: 1;
}

.user-menu-name {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-menu-email {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-top: 2px;
}

.user-menu-divider {
  height: 1px;
  background: var(--border-secondary);
  margin: var(--spacing-1) 0;
}

.user-menu-section {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.user-menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: var(--radius-md);
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: var(--font-size-sm);
  cursor: pointer;
  text-align: left;
  width: 100%;
  transition: all var(--transition-fast);
  font-family: inherit;
}

.user-menu-item svg {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: var(--text-tertiary);
  transition: color var(--transition-fast);
}

.user-menu-item:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.user-menu-item:hover svg {
  color: var(--text-primary);
}

.user-menu-item.danger {
  color: var(--error-text);
}

.user-menu-item.danger svg {
  color: var(--error-text);
  opacity: 0.8;
}

.user-menu-item.danger:hover {
  background: var(--error-bg);
  color: var(--error-text);
}

.user-menu-item.danger:hover svg {
  color: var(--error-text);
}

.user-menu-item:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.user-menu-item:disabled:hover {
  background: transparent;
  color: var(--text-secondary);
}

.menu-soon {
  margin-left: auto;
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
}

.app-content {
  flex: 1;
  padding: var(--spacing-6);
  max-width: 1200px;
  width: 100%;
  margin: 0 auto;
  box-sizing: border-box;
}

/* 移动端抽屉遮罩 */
.drawer-backdrop {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: calc(var(--z-drawer) - 1);
  animation: backdropFadeIn 0.2s ease;
}

@keyframes backdropFadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

/* 移动端抽屉 */
.mobile-drawer {
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  width: 280px;
  max-width: 85vw;
  background: var(--bg-elevated);
  border-right: 1px solid var(--border-primary);
  z-index: var(--z-drawer);
  display: flex;
  flex-direction: column;
  transform: translateX(-100%);
  transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: var(--shadow-xl);
}

.mobile-drawer.open {
  transform: translateX(0);
}

.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  border-bottom: 1px solid var(--border-primary);
}

.drawer-user {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 0;
}

.drawer-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: var(--bg-tertiary);
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  text-transform: uppercase;
  flex-shrink: 0;
}

.drawer-user-info {
  min-width: 0;
  flex: 1;
}

.drawer-username {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.drawer-user-email {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-top: 2px;
}

.drawer-close {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--transition-fast);
  flex-shrink: 0;
}

.drawer-close:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.drawer-close svg {
  width: 16px;
  height: 16px;
}

.drawer-nav {
  flex: 1;
  overflow-y: auto;
  padding: 12px 8px;
}

.drawer-nav-section {
  margin-bottom: 16px;
}

.drawer-nav-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 8px 12px 6px;
}

.drawer-nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  font-size: var(--font-size-sm);
  text-decoration: none;
  cursor: pointer;
  transition: all var(--transition-fast);
  position: relative;
  font-family: inherit;
  border: none;
  background: transparent;
  width: 100%;
  text-align: left;
}

.drawer-nav-item svg {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  color: var(--text-tertiary);
  transition: color var(--transition-fast);
}

.drawer-nav-item:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.drawer-nav-item:hover svg {
  color: var(--text-primary);
}

.drawer-nav-item.router-link-active {
  background: var(--bg-tertiary);
  color: var(--text-primary);
  font-weight: 500;
}

.drawer-nav-item.router-link-active svg {
  color: var(--text-primary);
}

.drawer-nav-item:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.drawer-nav-item:disabled:hover {
  background: transparent;
  color: var(--text-secondary);
}

.drawer-nav-item:disabled:hover svg {
  color: var(--text-tertiary);
}

.drawer-nav-dot {
  margin-left: auto;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-primary);
}

.drawer-footer {
  padding: 12px 16px 20px;
  border-top: 1px solid var(--border-primary);
}

.drawer-logout {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  border: none;
  background: transparent;
  color: var(--error-text);
  font-size: var(--font-size-sm);
  cursor: pointer;
  width: 100%;
  transition: all var(--transition-fast);
  font-family: inherit;
}

.drawer-logout svg {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.drawer-logout:hover {
  background: var(--error-bg);
}

/* 响应式断点 */
@media (max-width: 768px) {
  .app-header {
    padding: 0 12px;
    gap: 8px;
  }

  .desktop-nav {
    display: none;
  }

  .menu-btn {
    display: flex;
  }

  .app-nav {
    margin-left: 0;
  }

  .app-content {
    padding: 16px;
  }

  .app-brand-text {
    font-size: var(--font-size-sm);
  }
}

@media (max-width: 480px) {
  .app-content {
    padding: 12px;
  }

  .user-name {
    display: none;
  }
}
</style>
