/**
 * useTheme — 全局主题切换 composable
 *
 * 主题存储：
 *   localStorage key: 'app-theme'
 *   值: 'light' | 'dark'
 *
 * 优先级：
 *   1. localStorage 中的用户选择（最高）
 *   2. 系统 prefers-color-scheme（首次访问时跟随）
 *   3. 默认 light
 *
 * 实现方式：在 <html> 上设置 data-theme 属性，CSS 通过属性选择器切换 token
 */
import { ref, onMounted, onUnmounted } from 'vue'

const STORAGE_KEY = 'app-theme'
const theme = ref('light')
let initialized = false
let mediaQuery = null

function applyTheme(newTheme) {
  document.documentElement.setAttribute('data-theme', newTheme)
}

function readInitialTheme() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') {
      return stored
    }
  } catch (e) {
    // localStorage 不可用，回退
  }
  if (typeof window !== 'undefined' && window.matchMedia) {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  return 'light'
}

function setTheme(newTheme) {
  if (newTheme !== 'light' && newTheme !== 'dark') return
  theme.value = newTheme
  applyTheme(newTheme)
  try {
    localStorage.setItem(STORAGE_KEY, newTheme)
  } catch (e) {
    // 忽略存储失败
  }
}

function toggleTheme() {
  setTheme(theme.value === 'dark' ? 'light' : 'dark')
}

function handleSystemChange(e) {
  // 仅当用户没有手动选择过时才跟随系统
  try {
    if (localStorage.getItem(STORAGE_KEY)) return
  } catch (e) {
    // ignore
  }
  const newTheme = e.matches ? 'dark' : 'light'
  theme.value = newTheme
  applyTheme(newTheme)
}

export function useTheme() {
  onMounted(() => {
    if (!initialized) {
      initialized = true
      // index.html 中的内联脚本已经设置好了 data-theme，
      // 这里同步一下 ref 状态
      const current = document.documentElement.getAttribute('data-theme')
      theme.value = current === 'dark' ? 'dark' : 'light'

      // 监听系统主题变化（仅用户未手动选择时生效）
      if (window.matchMedia) {
        mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
        if (mediaQuery.addEventListener) {
          mediaQuery.addEventListener('change', handleSystemChange)
        } else if (mediaQuery.addListener) {
          // Safari 旧版本兼容
          mediaQuery.addListener(handleSystemChange)
        }
      }
    }
  })

  onUnmounted(() => {
    // 注意：不清理监听器，因为主题是全局单例状态
    // 这里保留 onUnmounted 钩子以符合 composable 规范
  })

  return {
    theme,
    setTheme,
    toggleTheme,
  }
}
