<template>
  <AppLayout :username="auth.username" @logout="onLogout">
    <section class="task-list-page">
      <!-- 页头 -->
      <div class="page-head">
        <div>
          <h2>我的任务</h2>
          <p class="page-subtitle">共 {{ store.tasks.length }} 个转写任务</p>
        </div>
        <button class="btn btn-primary" @click="goCreate">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M8 3v10M3 8h10" stroke-linecap="round" />
          </svg>
          提交新任务
        </button>
      </div>

      <!-- 骨架屏加载 -->
      <div v-if="store.loading && !store.tasks.length" class="task-skeleton-list">
        <div v-for="i in 4" :key="i" class="task-skeleton-card">
          <div class="skeleton-line title-line"></div>
          <div class="skeleton-row">
            <div class="skeleton-tag"></div>
            <div class="skeleton-tag small"></div>
            <div class="skeleton-text"></div>
          </div>
          <div class="skeleton-progress">
            <div class="skeleton-bar"></div>
          </div>
        </div>
      </div>

      <!-- 空态 -->
      <div v-else-if="!store.loading && store.tasks.length === 0" class="empty-state">
        <div class="empty-icon">
          <svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
            <rect x="12" y="8" width="40" height="48" rx="3" stroke="currentColor" stroke-width="2" />
            <path d="M20 20h24M20 28h18M20 36h20M20 44h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-opacity="0.4" />
            <path d="M36 8v6M36 14h-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
        </div>
        <h3 class="empty-title">还没有转写任务</h3>
        <p class="empty-desc">上传一篇论文，开始你的第一次易读化转写</p>
        <button class="btn btn-primary" @click="goCreate">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M8 3v10M3 8h10" stroke-linecap="round" />
          </svg>
          新建任务
        </button>
      </div>

      <!-- 任务卡片列表 -->
      <div v-else class="task-card-list">
        <div
          v-for="task in store.tasks"
          :key="task.id"
          class="task-card"
          :class="`status-${task.status}`"
          @click="goDetail(task.id)"
        >
          <div class="card-main">
            <div class="card-top">
              <div class="card-title-row">
                <h3 class="card-title">{{ displayTitle(task) }}</h3>
                <svg class="card-arrow" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M6 3l5 5-5 5" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </div>
              <div class="card-tags">
                <span class="status-tag" :class="`tag-${task.status}`">
                  <span class="status-dot"></span>
                  {{ statusMeta(task.status).label }}
                </span>
                <span class="source-tag">
                  {{ task.source_kind === 'arxiv' ? 'arXiv' : 'PDF' }}
                </span>
                <span v-if="task.fake_mode" class="demo-tag">演示</span>
              </div>
            </div>

            <div class="card-meta">
              <span class="meta-item">
                <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5">
                  <rect x="2.5" y="1.5" width="9" height="11" rx="1.5" />
                  <path d="M2.5 4.5h9" stroke-linecap="round" />
                  <path d="M4.5 1v2M9.5 1v2" stroke-linecap="round" />
                </svg>
                {{ formatTime(task.created_at) }}
              </span>
              <span v-if="task.result" class="meta-item">
                <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M2.5 3.5h9v7h-9z" stroke-linejoin="round" />
                  <path d="M4.5 5.5h5M4.5 7.5h5M4.5 9.5h3" stroke-linecap="round" />
                </svg>
                {{ task.result.section_count }} 章
              </span>
              <span v-if="task.result" class="meta-item">
                <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M7 2v5l3 2" stroke-linecap="round" stroke-linejoin="round" />
                  <circle cx="7" cy="7" r="5.5" />
                </svg>
                {{ formatNumber(task.result.total_chars) }} 字
              </span>
            </div>
          </div>

          <!-- 运行中进度条 -->
          <div v-if="isActive(task.status)" class="card-progress">
            <div class="progress-track">
              <div
                class="progress-fill"
                :style="{ width: Math.round((task.progress || 0) * 100) + '%' }"
              ></div>
            </div>
            <span class="progress-text">
              {{ STAGE_LABEL[task.stage] || task.stage }}
              · {{ Math.round((task.progress || 0) * 100) }}%
            </span>
          </div>

          <!-- 失败信息 -->
          <div v-else-if="task.failure" class="card-failure">
            <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5">
              <circle cx="7" cy="7" r="5.5" />
              <path d="M7 4v3M7 8.5v.5" stroke-linecap="round" />
            </svg>
            <span>{{ failMeta(task.failure.category).label }} · {{ task.failure.code }}</span>
          </div>

          <!-- 底部操作按钮 -->
          <div class="card-actions">
            <button
              v-if="task.status === 'succeeded'"
              class="btn btn-ghost btn-sm"
              @click.stop="goReader(task.id)"
            >
              <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M1.5 3.5l5.5 4 5.5-4" stroke-linecap="round" stroke-linejoin="round" />
                <path d="M2 3.5v7l5 3.5 5-3.5v-7" stroke-linejoin="round" />
              </svg>
              阅读
            </button>
            <button
              v-if="task.status === 'succeeded'"
              class="btn btn-ghost btn-sm"
              :loading="downloadingId === task.id"
              @click.stop="onDownload(task)"
            >
              <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M7 2v6M4.5 5.5L7 8l2.5-2.5" stroke-linecap="round" stroke-linejoin="round" />
                <path d="M2.5 10.5v1h9v-1" stroke-linecap="round" />
              </svg>
              下载
            </button>
            <button
              v-if="isActive(task.status)"
              class="btn btn-ghost btn-sm btn-warn"
              @click.stop="onCancel(task)"
            >
              取消任务
            </button>
            <button
              v-if="!isActive(task.status)"
              class="btn btn-ghost btn-sm btn-danger"
              @click.stop="onDelete(task)"
            >
              <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M2.5 3.5h9M5.5 3.5V2.5h3v1M4 3.5l.5 8h5l.5-8" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
              删除
            </button>
            <button class="btn btn-text btn-sm" @click.stop="goDetail(task.id)">
              查看详情
            </button>
          </div>
        </div>
      </div>
    </section>
  </AppLayout>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useDialog, useMessage } from 'naive-ui'
import AppLayout from '../layouts/AppLayout.vue'
import { useAuthStore } from '../stores/auth'
import { useTasksStore } from '../stores/tasks'
import { cancelTask } from '../api/tasks'
import { downloadArtifactZip } from '../api/artifacts'
import { FAILURE_CATEGORY_META, STAGE_LABEL, STATUS_META } from '../constants/tasks'

const router = useRouter()
const auth = useAuthStore()
const store = useTasksStore()
const dialog = useDialog()
const message = useMessage()
const downloadingId = ref(null)

function statusMeta(status) {
  return STATUS_META[status] || { label: status }
}
function failMeta(category) {
  return FAILURE_CATEGORY_META[category] || { label: category }
}
function isActive(status) {
  return status === 'queued' || status === 'running'
}
function displayTitle(task) {
  return task.result?.title || task.source_ref
}
function formatTime(value) {
  if (!value) return ''
  const d = new Date(value)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
function formatNumber(num) {
  if (num >= 10000) {
    return (num / 10000).toFixed(1) + '万'
  }
  return num.toLocaleString()
}

function goCreate() {
  router.push('/tasks/new')
}
function goDetail(id) {
  router.push(`/tasks/${id}`)
}
function goReader(id) {
  router.push(`/tasks/${id}/reader`)
}

async function onDownload(task) {
  if (downloadingId.value) return
  downloadingId.value = task.id
  try {
    await downloadArtifactZip(task.id)
  } catch (e) {
    if (e.status !== 401) {
      message.error(e.message || '下载失败，请稍后重试')
    }
  } finally {
    downloadingId.value = null
  }
}

function onCancel(task) {
  dialog.warning({
    title: '取消任务',
    content: `确定取消「${displayTitle(task)}」吗？取消在当前处理阶段结束后生效，且不可恢复。`,
    positiveText: '取消任务',
    negativeText: '再想想',
    onPositiveClick: async () => {
      try {
        await cancelTask(task.id)
        message.success('取消请求已受理')
        store.trackTask(task.id)
      } catch (e) {
        if (e.response?.status === 409) {
          message.warning('任务已结束，无需取消')
          store.fetchList()
        } else if (e.response?.status === 404) {
          message.error('任务不存在')
          store.fetchList()
        } else if (e.response?.status !== 401) {
          message.error('取消失败，请稍后重试')
        }
      }
    },
  })
}

function onDelete(task) {
  dialog.error({
    title: '删除任务',
    content: `确定删除「${displayTitle(task)}」吗？原始 PDF 与全部转写结果将被永久删除，且不可恢复。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await store.removeTask(task.id)
        message.success('任务已删除')
      } catch (e) {
        if (e.response?.status === 409) {
          message.warning('任务进行中，请先取消')
          store.fetchList()
        } else if (e.response?.status === 404) {
          message.error('任务不存在')
          store.fetchList()
        } else if (e.response?.status !== 401) {
          message.error('删除失败，请稍后重试')
        }
      }
    },
  })
}

async function onLogout() {
  await auth.logout()
  router.push('/login')
}

onMounted(async () => {
  if (!auth.user && auth.isAuthenticated) await auth.fetchMe().catch(() => {})
  await store.fetchList()
  store.attachList()
})

onUnmounted(() => {
  store.detachList()
})
</script>

<style scoped>
.task-list-page {
  max-width: 860px;
  margin: 0 auto;
}

/* 页头 */
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: var(--spacing-6);
}

.page-head h2 {
  margin: 0;
  font-size: var(--font-size-2xl);
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.3px;
}

.page-subtitle {
  margin: 4px 0 0;
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
}

/* 按钮 */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: var(--radius-md);
  font-size: var(--font-size-sm);
  font-weight: 500;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all var(--transition-fast);
  font-family: inherit;
}

.btn svg {
  width: 14px;
  height: 14px;
}

.btn-sm {
  padding: 6px 12px;
  font-size: var(--font-size-xs);
}

.btn-primary {
  background: var(--text-primary);
  color: var(--text-inverse);
  border-color: var(--text-primary);
}

.btn-primary:hover {
  background: var(--text-secondary);
  border-color: var(--text-secondary);
  transform: translateY(-1px);
}

.btn-ghost {
  background: transparent;
  color: var(--text-secondary);
  border-color: var(--border-primary);
}

.btn-ghost:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
  border-color: var(--border-strong);
}

.btn-warn {
  color: var(--warning-text);
  border-color: var(--warning-border);
}

.btn-warn:hover {
  background: var(--warning-bg);
  color: var(--warning-text);
  border-color: var(--warning-text);
}

.btn-danger {
  color: var(--error-text);
  border-color: var(--error-border);
}

.btn-danger:hover {
  background: var(--error-bg);
  color: var(--error-text);
  border-color: var(--error-text);
}

.btn-text {
  background: transparent;
  color: var(--text-tertiary);
  padding: 6px 8px;
}

.btn-text:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}

/* 骨架屏 */
.task-skeleton-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.task-skeleton-card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-lg);
  padding: 20px;
}

.skeleton-line {
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  height: 18px;
  margin-bottom: 14px;
  position: relative;
  overflow: hidden;
}

.title-line {
  width: 60%;
  height: 20px;
}

.skeleton-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}

.skeleton-tag {
  width: 60px;
  height: 20px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-full);
  position: relative;
  overflow: hidden;
}

.skeleton-tag.small {
  width: 40px;
}

.skeleton-text {
  flex: 1;
  height: 14px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  max-width: 120px;
  margin-left: auto;
  position: relative;
  overflow: hidden;
}

.skeleton-progress {
  height: 6px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-full);
  position: relative;
  overflow: hidden;
}

.skeleton-bar {
  width: 40%;
  height: 100%;
  background: var(--bg-secondary);
  border-radius: var(--radius-full);
  position: relative;
  overflow: hidden;
}

.skeleton-line::after,
.skeleton-tag::after,
.skeleton-text::after,
.skeleton-bar::after {
  content: '';
  position: absolute;
  top: 0;
  left: -100%;
  width: 100%;
  height: 100%;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255, 255, 255, 0.6) 50%,
    transparent 100%
  );
  animation: shimmer 1.5s infinite;
}

@keyframes shimmer {
  100% {
    left: 100%;
  }
}

/* 空态 */
.empty-state {
  text-align: center;
  padding: 80px 20px;
}

.empty-icon {
  width: 64px;
  height: 64px;
  margin: 0 auto 20px;
  color: var(--text-quaternary);
  display: flex;
  align-items: center;
  justify-content: center;
}

.empty-icon svg {
  width: 64px;
  height: 64px;
}

.empty-title {
  margin: 0 0 8px;
  font-size: var(--font-size-lg);
  font-weight: 600;
  color: var(--text-primary);
}

.empty-desc {
  margin: 0 0 24px;
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
}

/* 任务卡片列表 */
.task-card-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.task-card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-lg);
  padding: 20px;
  cursor: pointer;
  transition: all var(--transition-normal);
}

.task-card:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}

.card-main {
  margin-bottom: 16px;
}

.card-top {
  margin-bottom: 12px;
}

.card-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.card-title {
  margin: 0;
  font-size: var(--font-size-md);
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.5;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.card-arrow {
  width: 16px;
  height: 16px;
  color: var(--text-quaternary);
  flex-shrink: 0;
  transition: all var(--transition-fast);
  margin-top: 2px;
}

.task-card:hover .card-arrow {
  color: var(--text-primary);
  transform: translateX(2px);
}

.card-tags {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.status-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  font-size: var(--font-size-xs);
  font-weight: 500;
  line-height: 1.4;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.tag-succeeded {
  background: var(--success-bg);
  color: var(--success-text);
}
.tag-succeeded .status-dot {
  background: var(--success-text);
}

.tag-running {
  background: var(--info-bg);
  color: var(--info-text);
}
.tag-running .status-dot {
  background: var(--info-text);
  animation: pulse 2s infinite;
}

.tag-queued {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}
.tag-queued .status-dot {
  background: var(--text-tertiary);
}

.tag-failed {
  background: var(--error-bg);
  color: var(--error-text);
}
.tag-failed .status-dot {
  background: var(--error-text);
}

.tag-cancelled {
  background: var(--bg-tertiary);
  color: var(--text-tertiary);
}
.tag-cancelled .status-dot {
  background: var(--text-quaternary);
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.source-tag {
  display: inline-flex;
  align-items: center;
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  font-size: var(--font-size-xs);
  font-weight: 500;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.demo-tag {
  display: inline-flex;
  align-items: center;
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  font-size: var(--font-size-xs);
  font-weight: 500;
  background: var(--warning-bg);
  color: var(--warning-text);
}

.card-meta {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
}

.meta-item svg {
  width: 14px;
  height: 14px;
}

/* 进度条 */
.card-progress {
  margin-bottom: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border-secondary);
}

.progress-track {
  height: 4px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-full);
  margin-bottom: 6px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--text-primary);
  border-radius: var(--radius-full);
  transition: width 0.3s ease;
}

.progress-text {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
}

/* 失败信息 */
.card-failure {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border-secondary);
  font-size: var(--font-size-xs);
  color: var(--error-text);
}

.card-failure svg {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}

/* 底部操作 */
.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-actions .btn-text {
  margin-left: auto;
}

/* 响应式 */
@media (max-width: 640px) {
  .page-head {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }

  .page-head h2 {
    font-size: var(--font-size-xl);
  }

  .task-card {
    padding: 16px;
  }

  .card-meta {
    gap: 12px;
  }

  .card-actions {
    flex-wrap: wrap;
  }

  .empty-state {
    padding: 60px 16px;
  }

  .empty-icon {
    width: 48px;
    height: 48px;
  }

  .empty-icon svg {
    width: 48px;
    height: 48px;
  }
}

@media (max-width: 400px) {
  .card-title-row {
    gap: 8px;
  }

  .card-arrow {
    display: none;
  }
}
</style>
