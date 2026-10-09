<template>
  <AppLayout :username="auth.username" @logout="onLogout">
    <div class="reader-page reader-container" :data-reader-theme="readerTheme">
      <!-- 顶部工具栏 -->
      <div class="reader-toolbar">
        <div class="toolbar-left">
          <button class="toolbar-link" @click="goDetail">
            <span class="toolbar-icon">←</span>
            返回任务详情
          </button>
          <span class="toolbar-title" v-if="reader">
            <strong>{{ reader.title }}</strong>
            <n-tag v-if="reader.fake_mode" size="small" :bordered="false" class="fake-tag">演示任务</n-tag>
          </span>
        </div>
        <div class="toolbar-right">
          <div class="toolbar-group">
            <button class="toolbar-btn" @click="decreaseFontSize" title="减小字号" :disabled="fontSizeIndex === 0">
              A-
            </button>
            <button class="toolbar-btn" @click="increaseFontSize" title="增大字号" :disabled="fontSizeIndex === fontSizeLevels.length - 1">
              A+
            </button>
          </div>
          <div class="toolbar-divider"></div>
          <div class="toolbar-group">
            <button
              v-for="t in themes"
              :key="t.value"
              class="theme-icon-btn"
              :class="{ active: readerTheme === t.value }"
              @click="setReaderTheme(t.value)"
              :title="t.label"
            >
              {{ t.icon }}
            </button>
          </div>
          <div class="toolbar-divider"></div>
          <button class="toolbar-btn download-btn" :loading="downloading" @click="onDownload">
            <span v-if="!downloading">⬇ 下载</span>
          </button>
        </div>
      </div>

      <!-- 阅读进度条 -->
      <div class="reader-progress-track">
        <div class="reader-progress-fill" :style="{ width: progressPercent + '%' }"></div>
      </div>

      <n-spin :show="loading">
        <!-- 错误态 -->
        <n-result
          v-if="error"
          :status="errorStatus"
          :title="error.title"
          :description="error.description"
        >
          <template #footer>
            <n-space>
              <n-button @click="goDetail">返回任务详情</n-button>
              <n-button v-if="retryable" @click="load">重试</n-button>
            </n-space>
          </template>
        </n-result>

        <div v-else-if="reader" class="reader-main">
          <!-- 左侧章节导航 -->
          <nav class="reader-sidebar" aria-label="章节导航">
            <div class="reader-sidebar-label">目录</div>
            <ul>
              <li
                v-for="s in reader.sections"
                :key="s.slug"
                :class="{ active: activeSlug === s.slug }"
              >
                <a :href="`#${s.slug}`" @click.prevent="jumpTo(s.slug)">
                  {{ s.heading }}
                </a>
              </li>
            </ul>
          </nav>

          <!-- 正文区 -->
          <main ref="bodyRef" class="reader-article" @click="onArticleClick">
            <!-- 文章标题 -->
            <header class="article-header">
              <h1 class="article-title">{{ reader.title }}</h1>
              <div class="reader-meta">
                <span class="meta-item">
                  <span>{{ reader.sections.length }} 章</span>
                </span>
                <span class="meta-divider"></span>
                <span class="meta-item">
                  约 {{ formatNumber(reader.total_chars) }} 字
                </span>
                <span class="meta-divider"></span>
                <span class="meta-item">
                  预计阅读 {{ estimatedReadTime }} 分钟
                </span>
              </div>
            </header>

            <!-- 各章节内容 -->
            <section
              v-for="s in reader.sections"
              :id="s.slug"
              :key="`${s.slug}-body`"
              class="reader-section"
              :data-slug="s.slug"
            >
              <h2 class="section-title">{{ s.heading }}</h2>
              <!-- eslint-disable-next-line vue/no-v-html -->
              <div class="section-html" v-html="rendered[s.slug]"></div>
            </section>
          </main>
        </div>
      </n-spin>

      <!-- 返回顶部按钮 -->
      <button
        class="back-to-top"
        :class="{ visible: showBackToTop }"
        @click="scrollToTop"
        title="返回顶部"
      >
        ↑
      </button>

      <!-- 图片灯箱 -->
      <div v-if="lightboxSrc" class="image-lightbox" @click.self="closeLightbox">
        <button class="lightbox-close" @click="closeLightbox" title="关闭 (ESC)">✕</button>
        <img :src="lightboxSrc" alt="预览图片" />
      </div>
    </div>
  </AppLayout>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NResult, NSpace, NSpin, NTag, useMessage } from 'naive-ui'
import AppLayout from '../layouts/AppLayout.vue'
import { useAuthStore } from '../stores/auth'
import {
  downloadArtifactZip,
  fetchImageBlob,
  getReader,
  revokeTaskImages,
} from '../api/artifacts'
import { renderMarkdown } from '../lib/markdown'
import '../styles/reader.css'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const message = useMessage()

const taskId = route.params.id
const loading = ref(true)
const downloading = ref(false)
const reader = ref(null)
const bodyRef = ref(null)
const rendered = reactive({})
const activeSlug = ref('')
const progressPercent = ref(0)
const showBackToTop = ref(false)
const lightboxSrc = ref('')

// --- 阅读主题 ---
const STORAGE_KEY = 'reader-prefs'
const themes = [
  { value: 'light', label: '浅色主题', icon: '☼' },
  { value: 'eye-care', label: '护眼主题', icon: '◎' },
  { value: 'dark', label: '暗色主题', icon: '☾' },
]

function getInitialTheme() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      const prefs = JSON.parse(stored)
      if (prefs.theme && themes.some((t) => t.value === prefs.theme)) {
        return prefs.theme
      }
    }
  } catch (e) {
    // ignore
  }
  // 默认跟随全局主题
  const globalTheme = document.documentElement.getAttribute('data-theme')
  return globalTheme === 'dark' ? 'dark' : 'light'
}

const readerTheme = ref(getInitialTheme())

function setReaderTheme(theme) {
  readerTheme.value = theme
  savePrefs()
}

function savePrefs() {
  try {
    const prefs = {
      theme: readerTheme.value,
      fontSize: fontSizeLevels[fontSizeIndex.value],
      lineHeight: lineHeightLevels[lineHeightIndex.value],
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs))
  } catch (e) {
    // ignore
  }
}

// --- 字号调节 ---
const fontSizeLevels = [13, 14, 15, 16, 18] // px
const defaultFontSizeIndex = 2 // 15px
const fontSizeIndex = ref(defaultFontSizeIndex)

function increaseFontSize() {
  if (fontSizeIndex.value < fontSizeLevels.length - 1) {
    fontSizeIndex.value++
    updateReaderFontVars()
    savePrefs()
  }
}

function decreaseFontSize() {
  if (fontSizeIndex.value > 0) {
    fontSizeIndex.value--
    updateReaderFontVars()
    savePrefs()
  }
}

// --- 行高调节 ---
const lineHeightLevels = [1.6, 1.8, 2.0]
const defaultLineHeightIndex = 1 // 1.8
const lineHeightIndex = ref(defaultLineHeightIndex)

function updateReaderFontVars() {
  // 通过设置 CSS 变量来更新
  const container = document.querySelector('.reader-container')
  if (container) {
    container.style.setProperty('--reader-font-size', fontSizeLevels[fontSizeIndex.value] + 'px')
    container.style.setProperty('--reader-line-height', lineHeightLevels[lineHeightIndex.value])
  }
}

// 从 localStorage 恢复字号行高偏好
function restorePrefs() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      const prefs = JSON.parse(stored)
      if (prefs.fontSize) {
        const idx = fontSizeLevels.indexOf(prefs.fontSize)
        if (idx >= 0) fontSizeIndex.value = idx
      }
      if (prefs.lineHeight) {
        const idx = lineHeightLevels.indexOf(prefs.lineHeight)
        if (idx >= 0) lineHeightIndex.value = idx
      }
    }
  } catch (e) {
    // ignore
  }
}

// --- 工具函数 ---
function formatNumber(num) {
  if (num >= 10000) {
    return (num / 10000).toFixed(1) + '万'
  }
  return num.toLocaleString()
}

const estimatedReadTime = computed(() => {
  if (!reader.value) return 0
  const chars = reader.value.total_chars || 0
  return Math.max(1, Math.round(chars / 350))
})

// --- 错误处理 ---
const errorState = reactive({
  kind: 'none',
  status: 'info',
  title: '',
  description: '',
})

const error = computed(() => {
  if (errorState.kind === 'none') return null
  return {
    title: errorState.title,
    description: errorState.description,
  }
})
const errorStatus = computed(() => errorState.status)
const retryable = computed(() =>
  ['network', 'artifact_missing', 'unknown'].includes(errorState.kind),
)

function setError(kind, status, title, description) {
  errorState.kind = kind
  errorState.status = status
  errorState.title = title
  errorState.description = description
}

function classifyError(e) {
  if (e.status === 404) {
    setError('not_found', '404', '任务不存在', '它可能已被删除，或不属于当前账号')
  } else if (e.status === 409 && e.code === 'task_not_ready') {
    setError('not_ready', 'info', '任务尚未完成', '暂无可阅读的成品')
  } else if (e.status === 409 && e.code === 'artifact_missing') {
    setError('artifact_missing', 'warning', '成品文件缺失或已损坏', '请重新提交任务')
  } else if (e.status === 401) {
    setError('unauthorized', '401', '登录已过期', '请重新登录后再打开')
  } else if (e.status === 0) {
    setError('network', 'info', '网络不可用', '请检查网络后重试')
  } else {
    setError('unknown', 'error', '成品加载失败', '请稍后重试')
  }
}

// --- 图片处理 ---
async function hydrateImages() {
  const root = bodyRef.value
  if (!root) return
  const imgs = Array.from(root.querySelectorAll('img'))
  await Promise.all(
    imgs.map(async (img) => {
      const name = img.getAttribute('data-art')
      try {
        if (!name) throw new Error('invalid')
        const url = await fetchImageBlob(taskId, name)
        img.src = url
        img.setAttribute('data-lightbox', 'true')
      } catch {
        const placeholder = document.createElement('div')
        placeholder.className = 'art-missing'
        placeholder.textContent = '图片暂不可用'
        img.replaceWith(placeholder)
      }
    }),
  )
}

// --- 图片灯箱 ---
function onArticleClick(e) {
  const img = e.target.closest('img[data-lightbox]')
  if (img) {
    lightboxSrc.value = img.src
    document.body.style.overflow = 'hidden'
  }
}

function closeLightbox() {
  lightboxSrc.value = ''
  document.body.style.overflow = ''
}

function onKeydown(e) {
  if (e.key === 'Escape' && lightboxSrc.value) {
    closeLightbox()
  }
}

// --- 滚动相关 ---
let scrollHandler = null
let observer = null

function updateScrollState() {
  const scrollTop = window.scrollY || document.documentElement.scrollTop
  const scrollHeight = document.documentElement.scrollHeight
  const clientHeight = document.documentElement.clientHeight
  const scrollable = scrollHeight - clientHeight

  // 进度
  if (scrollable > 0) {
    progressPercent.value = Math.min(100, Math.max(0, (scrollTop / scrollable) * 100))
  }

  // 返回顶部按钮
  showBackToTop.value = scrollTop > 400
}

function setupScrollSpy() {
  teardownObserver()
  if (typeof IntersectionObserver === 'undefined') return
  observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((en) => en.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
      if (visible.length > 0) {
        activeSlug.value = visible[0].target.dataset.slug
      }
    },
    { rootMargin: '-30% 0px -60% 0px', threshold: 0 },
  )
  bodyRef.value
    ?.querySelectorAll('.reader-section')
    .forEach((el) => observer.observe(el))
}

function teardownObserver() {
  if (observer) {
    observer.disconnect()
    observer = null
  }
}

function jumpTo(slug) {
  const el = document.getElementById(slug)
  if (!el) return
  activeSlug.value = slug
  history.replaceState(null, '', `#${slug}`)
  el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function scrollToTop() {
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function applyInitialHash() {
  const hash = (route.hash || '').replace(/^#/, '')
  const slugs = new Set(reader.value.sections.map((s) => s.slug))
  if (hash && slugs.has(hash)) {
    activeSlug.value = hash
    nextTick(() => {
      const el = document.getElementById(hash)
      if (el) el.scrollIntoView()
    })
  } else {
    activeSlug.value = reader.value.sections[0]?.slug || ''
    window.scrollTo(0, 0)
  }
}

// --- 折叠区动画包装 ---
function wrapDetailsWithAnimation() {
  const root = bodyRef.value
  if (!root) return
  const detailsList = root.querySelectorAll('details')
  detailsList.forEach((details) => {
    if (details.classList.contains('wrapped')) return
    details.classList.add('wrapped')

    // 把 summary 之后的所有内容包到 .detail-inner > div 中
    const summary = details.querySelector('summary')
    if (!summary) return

    // 创建内层包裹
    const inner = document.createElement('div')
    inner.className = 'detail-inner'
    const contentDiv = document.createElement('div')
    const contentWrapper = document.createElement('div')
    contentWrapper.className = 'detail-content'

    // 移动 summary 之后的所有节点到 contentWrapper
    const nodesToMove = []
    let next = summary.nextSibling
    while (next) {
      nodesToMove.push(next)
      next = next.nextSibling
    }
    nodesToMove.forEach((node) => contentWrapper.appendChild(node))

    contentDiv.appendChild(contentWrapper)
    inner.appendChild(contentDiv)
    details.appendChild(inner)
  })
}

// --- 数据加载 ---
async function load() {
  loading.value = true
  errorState.kind = 'none'
  try {
    const data = await getReader(taskId)
    reader.value = data
    for (const s of data.sections) {
      rendered[s.slug] = renderMarkdown(s.content)
    }
    activeSlug.value = data.sections[0]?.slug || ''
    await nextTick()
    applyInitialHash()
    setupScrollSpy()
    hydrateImages().then(() => {
      // 图片加载完后包装折叠区
      wrapDetailsWithAnimation()
    })
    // 先包装折叠区（图片可能还在加载，但折叠区结构已在）
    wrapDetailsWithAnimation()
  } catch (e) {
    reader.value = null
    classifyError(e)
  } finally {
    loading.value = false
  }
}

async function onDownload() {
  if (downloading.value) return
  downloading.value = true
  try {
    await downloadArtifactZip(taskId)
  } catch (e) {
    if (e.status !== 401) {
      message.error(e.message || '下载失败，请稍后重试')
    }
  } finally {
    downloading.value = false
  }
}

function goDetail() {
  router.push(`/tasks/${taskId}`)
}

async function onLogout() {
  await auth.logout()
  router.push('/login')
}

// --- 生命周期 ---
onMounted(async () => {
  restorePrefs()
  updateReaderFontVars()

  if (!auth.user && auth.isAuthenticated) await auth.fetchMe().catch(() => {})
  await load()

  // 滚动监听
  scrollHandler = () => {
    requestAnimationFrame(updateScrollState)
  }
  window.addEventListener('scroll', scrollHandler, { passive: true })
  updateScrollState()

  // 键盘监听
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  teardownObserver()
  revokeTaskImages(taskId)
  if (scrollHandler) {
    window.removeEventListener('scroll', scrollHandler)
  }
  window.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
})

// 监听主题变化，更新字号行高变量
watch(readerTheme, () => {
  nextTick(() => {
    updateReaderFontVars()
  })
})
</script>

<style scoped>
.reader-page {
  width: 100%;
  max-width: 100%;
  margin: calc(-1 * var(--spacing-6)) calc(-1 * var(--spacing-6)) 0;
  padding: 0 var(--spacing-6);
  min-height: calc(100vh - 56px);
  position: relative;
}

/* 顶部工具栏 */
.reader-toolbar {
  position: sticky;
  top: 56px;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 0;
  background: var(--reader-bg);
  border-bottom: 1px solid var(--reader-border-soft);
  transition: background-color var(--transition-normal);
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 16px;
  min-width: 0;
}

.toolbar-link {
  display: flex;
  align-items: center;
  gap: 6px;
  background: none;
  border: none;
  color: var(--reader-text-tertiary);
  font-size: 13px;
  cursor: pointer;
  padding: 4px 0;
  transition: color var(--transition-fast);
  white-space: nowrap;
}

.toolbar-link:hover {
  color: var(--reader-accent);
}

.toolbar-icon {
  font-size: 12px;
}

.toolbar-title {
  font-size: 13px;
  color: var(--reader-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 400px;
}

.toolbar-title strong {
  color: var(--reader-text-secondary);
  font-weight: 500;
  margin-right: 8px;
}

.fake-tag {
  transform: scale(0.9);
  transform-origin: left center;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.toolbar-group {
  display: flex;
  align-items: center;
  gap: 2px;
}

.toolbar-divider {
  width: 1px;
  height: 20px;
  background: var(--reader-border);
  margin: 0 4px;
}

.toolbar-btn {
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--reader-border);
  background: var(--reader-bg);
  color: var(--reader-text-secondary);
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  transition: all var(--transition-fast);
}

.toolbar-btn:hover:not(:disabled) {
  color: var(--reader-accent);
  border-color: var(--reader-text-tertiary);
  background: var(--reader-bg-soft);
}

.toolbar-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.download-btn {
  padding: 0 14px;
  font-weight: 600;
}

.theme-icon-btn {
  width: 30px;
  height: 30px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--reader-text-tertiary);
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--transition-fast);
}

.theme-icon-btn:hover {
  color: var(--reader-accent);
  background: var(--reader-bg-soft);
}

.theme-icon-btn.active {
  color: var(--reader-accent);
  background: var(--reader-bg-soft);
  border-color: var(--reader-border);
  font-weight: 600;
}

/* 阅读进度条 */
.reader-progress-track {
  height: 2px;
  background: var(--reader-border-soft);
  position: sticky;
  top: calc(56px + 46px);
  z-index: 9;
  margin: 0 calc(-1 * var(--spacing-6));
}

.reader-progress-fill {
  height: 100%;
  background: var(--reader-accent);
  transition: width 0.1s linear;
}

/* 主体布局 */
.reader-main {
  display: flex;
  gap: 48px;
  align-items: flex-start;
  padding: 32px 0 80px;
  position: relative;
}

/* 文章标题区 */
.article-header {
  margin-bottom: 24px;
}

.article-title {
  font-size: 1.8em;
  font-weight: 700;
  line-height: 1.4;
  margin: 0 0 12px;
  color: var(--reader-text);
  letter-spacing: -0.3px;
}

.reader-section {
  margin-bottom: 48px;
  scroll-margin-top: 100px;
}

.section-title {
  font-size: 1.4em;
  font-weight: 700;
  margin: 2em 0 1em;
  padding-left: 12px;
  border-left: 3px solid var(--reader-accent);
  line-height: 1.4;
  color: var(--reader-text);
}

/* scoped 穿透 —— 确保 Markdown 内容样式生效 */
.section-html {
  font-size: inherit;
  line-height: inherit;
}

.section-html :deep(p) {
  margin: 0.9em 0;
  text-align: justify;
  color: var(--reader-text);
}
</style>
