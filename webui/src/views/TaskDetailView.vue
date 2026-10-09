<template>
  <AppLayout :username="auth.username" @logout="onLogout">
    <section class="task-detail-page">
      <!-- 返回按钮 -->
      <div class="detail-head">
        <button class="btn btn-text back-btn" @click="goList">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M10 3L5 8l5 5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          返回任务列表
        </button>
      </div>

      <!-- 详情页骨架屏 -->
      <div v-if="store.detailLoading && !task" class="detail-skeleton">
        <div class="skeleton-card">
          <div class="skeleton-line title-line"></div>
          <div class="skeleton-row">
            <div class="skeleton-tag"></div>
            <div class="skeleton-tag small"></div>
          </div>
          <div class="skeleton-progress-bar">
            <div class="skeleton-bar-fill"></div>
          </div>
        </div>

        <div class="skeleton-card">
          <div class="skeleton-line section-title"></div>
          <div class="skeleton-stepper">
            <div v-for="i in 6" :key="i" class="skeleton-step">
              <div class="skeleton-circle"></div>
              <div class="skeleton-step-label"></div>
            </div>
          </div>
        </div>

        <div class="skeleton-card">
          <div class="skeleton-line section-title"></div>
          <div class="skeleton-result-grid">
            <div class="skeleton-result-item">
              <div class="skeleton-result-num"></div>
              <div class="skeleton-result-label"></div>
            </div>
            <div class="skeleton-result-item">
              <div class="skeleton-result-num"></div>
              <div class="skeleton-result-label"></div>
            </div>
            <div class="skeleton-result-item">
              <div class="skeleton-result-num"></div>
              <div class="skeleton-result-label"></div>
            </div>
            <div class="skeleton-result-item">
              <div class="skeleton-result-num"></div>
              <div class="skeleton-result-label"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- 404 -->
      <div v-else-if="notFound" class="empty-state detail-404">
        <div class="empty-icon">
          <svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="32" cy="32" r="28" stroke="currentColor" stroke-width="2" />
            <path d="M32 22v12M32 40v2" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
        </div>
        <h3 class="empty-title">任务不存在</h3>
        <p class="empty-desc">它可能已被删除，或不属于当前账号</p>
        <button class="btn btn-primary" @click="goList">返回任务列表</button>
      </div>

      <!-- 任务详情 -->
      <template v-else-if="task">
        <!-- 头部卡片 -->
        <div class="detail-card head-card">
          <div class="head-main">
            <div class="head-title-row">
              <h2 class="head-title">{{ task.source_ref }}</h2>
              <div class="head-tags">
                <span class="status-tag" :class="`tag-${task.status}`">
                  <span class="status-dot"></span>
                  {{ statusMeta(task.status).label }}
                </span>
                <span class="source-tag">
                  {{ task.source_kind === 'arxiv' ? 'arXiv' : 'PDF 上传' }}
                </span>
                <span v-if="task.fake_mode" class="demo-tag">演示</span>
              </div>
            </div>
            <div class="head-meta">
              <span class="meta-item">
                <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5">
                  <rect x="2.5" y="1.5" width="9" height="11" rx="1.5" />
                  <path d="M2.5 4.5h9" stroke-linecap="round" />
                  <path d="M4.5 1v2M9.5 1v2" stroke-linecap="round" />
                </svg>
                {{ formatTime(task.created_at) }}
              </span>
              <span v-if="task.updated_at" class="meta-item">
                <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M7 2v5l3 2" stroke-linecap="round" stroke-linejoin="round" />
                  <circle cx="7" cy="7" r="5.5" />
                </svg>
                最后更新 {{ formatTime(task.updated_at) }}
              </span>
              <button
                v-if="isActive"
                class="btn btn-ghost btn-sm btn-warn cancel-btn"
                @click="onCancel"
              >
                取消任务
              </button>
              <button
                v-else
                class="btn btn-ghost btn-sm btn-danger delete-btn"
                @click="onDelete"
              >
                <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M2.5 3.5h9M5.5 3.5V2.5h3v1M4 3.5l.5 8h5l.5-8" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                删除任务
              </button>
            </div>
          </div>

          <!-- 进度条（运行中显示） -->
          <div v-if="isActive" class="head-progress">
            <div class="progress-track">
              <div
                class="progress-fill"
                :style="{ width: percentage + '%' }"
              ></div>
            </div>
            <div class="progress-info">
              <span class="progress-stage">{{ STAGE_LABEL[task.stage] || '准备中' }}</span>
              <span class="progress-percent">{{ percentage }}%</span>
            </div>
          </div>

          <!-- 阶段消息 -->
          <p v-if="task.message && !isActive" class="stage-message">{{ task.message }}</p>
        </div>

        <!-- 处理阶段步骤条 -->
        <div class="detail-card stages-card">
          <h3 class="card-section-title">处理阶段</h3>
          <div class="custom-stepper">
            <div
              v-for="(stage, index) in STAGES"
              :key="stage.key"
              class="step-item"
              :class="getStepClass(index)"
            >
              <div class="step-connector" v-if="index > 0"></div>
              <div class="step-circle">
                <svg v-if="isStepDone(index)" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M3.5 8.5l3 3 6-6" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                <span v-else>{{ index + 1 }}</span>
                <div v-if="isStepCurrent(index)" class="step-pulse-ring"></div>
              </div>
              <div class="step-label">{{ stage.label }}</div>
            </div>
          </div>
        </div>

        <!-- 成功结果卡片 -->
        <div v-if="task.status === 'succeeded' && task.result" class="detail-card result-card">
          <div class="result-header">
            <div class="result-icon success-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z" />
                <path d="M8 12l3 3 5-6" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </div>
            <div class="result-header-text">
              <h3 class="result-title">转写完成</h3>
              <p class="result-subtitle">论文已成功转换为易读版本</p>
            </div>
          </div>

          <div class="result-stats">
            <div class="stat-item">
              <div class="stat-value">{{ task.result.title || '—' }}</div>
              <div class="stat-label">论文标题</div>
            </div>
            <div class="stat-row">
              <div class="stat-item small">
                <div class="stat-value num">{{ task.result.section_count || 0 }}</div>
                <div class="stat-label">章节数</div>
              </div>
              <div class="stat-item small">
                <div class="stat-value num">{{ formatNumber(task.result.total_chars) }}</div>
                <div class="stat-label">总字数</div>
              </div>
              <div class="stat-item small">
                <div class="stat-value num">{{ formatDuration(task.result.duration_seconds) }}</div>
                <div class="stat-label">处理时长</div>
              </div>
            </div>
          </div>

          <div class="result-actions">
            <button class="btn btn-primary" @click="goReader(task.id)">
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M1.5 3.5l5.5 4 5.5-4" stroke-linecap="round" stroke-linejoin="round" />
                <path d="M2 3.5v7l5 3.5 5-3.5v-7" stroke-linejoin="round" />
              </svg>
              阅读成品
            </button>
            <button class="btn btn-ghost" :disabled="downloading" @click="onDownload(task.id)">
              <svg v-if="!downloading" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M8 2v6M5.5 5.5L8 8l2.5-2.5" stroke-linecap="round" stroke-linejoin="round" />
                <path d="M2.5 10.5v1h9v-1" stroke-linecap="round" />
              </svg>
              <svg v-else class="spin-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M8 2.5a5.5 5.5 0 105.5 5.5" stroke-linecap="round" />
              </svg>
              {{ downloading ? '下载中...' : '下载 zip' }}
            </button>
          </div>
        </div>

        <!-- 失败状态卡片（弱差异化：同卡片样式，仅图标颜色不同） -->
        <div v-else-if="task.status === 'failed' && task.failure" class="detail-card result-card failure-card">
          <div class="result-header">
            <div class="result-icon" :class="getFailIconClass(task.failure.category)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <circle cx="12" cy="12" r="10" />
                <path d="M12 8v4M12 15v.5" stroke-linecap="round" />
              </svg>
            </div>
            <div class="result-header-text">
              <h3 class="result-title">任务未能完成</h3>
              <p class="result-subtitle">
                {{ failMeta(task.failure.category).label }} · {{ task.failure.code }}
              </p>
            </div>
          </div>

          <div class="failure-message">
            {{ task.failure.message }}
          </div>

          <div class="result-actions">
            <button class="btn btn-primary" @click="goCreate">
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M8 3v10M3 8h10" stroke-linecap="round" />
              </svg>
              重新提交
            </button>
            <button class="btn btn-ghost" @click="goList">返回列表</button>
          </div>
        </div>

        <!-- 已取消状态 -->
        <div v-else-if="task.status === 'cancelled'" class="detail-card result-card cancelled-card">
          <div class="result-header">
            <div class="result-icon cancelled-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <circle cx="12" cy="12" r="10" />
                <path d="M8 8l8 8M16 8l-8 8" stroke-linecap="round" />
              </svg>
            </div>
            <div class="result-header-text">
              <h3 class="result-title">任务已取消</h3>
              <p class="result-subtitle">任务在处理过程中被取消</p>
            </div>
          </div>
          <div class="result-actions">
            <button class="btn btn-primary" @click="goCreate">
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M8 3v10M3 8h10" stroke-linecap="round" />
              </svg>
              新建任务
            </button>
            <button class="btn btn-ghost" @click="goList">返回列表</button>
          </div>
        </div>
      </template>
    </section>
  </AppLayout>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDialog, useMessage } from 'naive-ui'
import AppLayout from '../layouts/AppLayout.vue'
import { useAuthStore } from '../stores/auth'
import { useTasksStore } from '../stores/tasks'
import { cancelTask } from '../api/tasks'
import { downloadArtifactZip } from '../api/artifacts'
import { FAILURE_CATEGORY_META, STAGES, STAGE_LABEL, STATUS_META } from '../constants/tasks'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const store = useTasksStore()
const dialog = useDialog()
const message = useMessage()
const notFound = ref(false)
const downloading = ref(false)

const task = computed(() => store.currentTask)
const isActive = computed(() =>
  task.value ? ['queued', 'running'].includes(task.value.status) : false,
)
const percentage = computed(() =>
  task.value ? Math.round((task.value.progress || 0) * 100) : 0,
)

function statusMeta(status) {
  return STATUS_META[status] || { label: status, type: 'default' }
}
function failMeta(category) {
  return FAILURE_CATEGORY_META[category] || { label: category, type: 'default' }
}

// 步骤状态判定
function getCurrentStepIndex() {
  if (!task.value?.stage) return task.value?.status === 'queued' ? 0 : -1
  const idx = STAGES.findIndex((s) => s.key === task.value.stage)
  return idx < 0 ? -1 : idx
}

function isStepDone(index) {
  const current = getCurrentStepIndex()
  if (task.value?.status === 'succeeded') return true
  if (task.value?.status === 'failed') return index < current
  if (task.value?.status === 'cancelled') return index < current
  return index < current
}

function isStepCurrent(index) {
  if (!isActive.value) return false
  return index === getCurrentStepIndex()
}

function getStepClass(index) {
  return {
    'step-done': isStepDone(index),
    'step-current': isStepCurrent(index),
    'step-pending': !isStepDone(index) && !isStepCurrent(index),
    'step-error': task.value?.status === 'failed' && index === getCurrentStepIndex(),
  }
}

function getFailIconClass(category) {
  if (category === 'input') return 'warning-icon'
  return 'error-icon'
}

function formatTime(value) {
  if (!value) return ''
  const d = new Date(value)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatNumber(num) {
  if (!num) return '0'
  if (num >= 10000) {
    return (num / 10000).toFixed(1) + ' 万'
  }
  return num.toLocaleString()
}

function formatDuration(seconds) {
  if (!seconds) return '—'
  if (seconds < 60) return `${Math.round(seconds)} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`
  return `${Math.floor(seconds / 3600)} 时 ${Math.floor((seconds % 3600) / 60)} 分`
}

function goList() {
  router.push('/tasks')
}
function goReader(id) {
  router.push(`/tasks/${id}/reader`)
}
function goCreate() {
  router.push('/tasks/new')
}

async function onDownload(id) {
  if (downloading.value) return
  downloading.value = true
  try {
    await downloadArtifactZip(id)
  } catch (e) {
    if (e.status !== 401) {
      message.error(e.message || '下载失败，请稍后重试')
    }
  } finally {
    downloading.value = false
  }
}

function onCancel() {
  const t = task.value
  if (!t) return
  dialog.warning({
    title: '取消任务',
    content: '取消在当前处理阶段结束后生效，且不可恢复。确定取消吗？',
    positiveText: '取消任务',
    negativeText: '再想想',
    onPositiveClick: async () => {
      try {
        await cancelTask(t.id)
        message.success('取消请求已受理')
        store.attachDetail(t.id)
      } catch (e) {
        if (e.response?.status === 409) {
          message.warning('任务已结束，无需取消')
          store.fetchDetail(t.id).catch(() => {})
        } else if (e.response?.status === 404) {
          message.error('任务不存在')
        } else if (e.response?.status !== 401) {
          message.error('取消失败，请稍后重试')
        }
      }
    },
  })
}

function onDelete() {
  const t = task.value
  if (!t) return
  dialog.error({
    title: '删除任务',
    content: '原始 PDF 与全部转写结果将被永久删除，且不可恢复。确定删除吗？',
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await store.removeTask(t.id)
        message.success('任务已删除')
        goList()
      } catch (e) {
        if (e.response?.status === 409) {
          message.warning('任务进行中，请先取消')
          store.fetchDetail(t.id).catch(() => {})
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
  try {
    await store.fetchDetail(route.params.id)
    store.attachDetail(route.params.id)
  } catch (e) {
    if (e.response?.status === 404) notFound.value = true
  }
})

onUnmounted(() => {
  store.detachDetail()
})
</script>

<style scoped>
.task-detail-page {
  max-width: 720px;
  margin: 0 auto;
}

/* 返回按钮 */
.detail-head {
  margin-bottom: 16px;
}

.back-btn {
  padding: 6px 10px;
  color: var(--text-tertiary);
  font-size: var(--font-size-sm);
}

.back-btn svg {
  width: 14px;
  height: 14px;
}

/* 通用卡片 */
.detail-card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-xl);
  padding: 24px;
  margin-bottom: 16px;
}

.card-section-title {
  margin: 0 0 20px;
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: 0.2px;
}

/* 按钮样式 */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 9px 18px;
  border-radius: var(--radius-md);
  font-size: var(--font-size-sm);
  font-weight: 500;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all var(--transition-fast);
  font-family: inherit;
}

.btn svg {
  width: 15px;
  height: 15px;
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

.btn-ghost:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  transform: none;
}

.btn-text {
  background: transparent;
  color: var(--text-tertiary);
  border: none;
  padding: 6px 8px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.btn-text:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
  border-radius: var(--radius-md);
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

.spin-icon {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* 头部卡片 */
.head-card {
  padding: 24px;
}

.head-main {
  margin-bottom: 0;
}

.head-title-row {
  margin-bottom: 12px;
}

.head-title {
  margin: 0 0 10px;
  font-size: var(--font-size-xl);
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.4;
  letter-spacing: -0.3px;
}

.head-tags {
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

.head-meta {
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

.cancel-btn {
  margin-left: auto;
}

.delete-btn {
  margin-left: auto;
}

/* 进度条 */
.head-progress {
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid var(--border-secondary);
}

.progress-track {
  height: 6px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-full);
  margin-bottom: 8px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--text-primary);
  border-radius: var(--radius-full);
  transition: width 0.3s ease;
}

.progress-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--font-size-xs);
}

.progress-stage {
  color: var(--text-secondary);
  font-weight: 500;
}

.progress-percent {
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}

.stage-message {
  margin: 12px 0 0;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  line-height: 1.6;
}

/* 自定义步骤条 */
.stages-card {
  padding: 24px;
}

.custom-stepper {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  position: relative;
}

.step-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
  position: relative;
}

.step-connector {
  position: absolute;
  top: 14px;
  right: 50%;
  width: 100%;
  height: 2px;
  background: var(--border-primary);
  z-index: 0;
}

.step-done .step-connector {
  background: var(--text-primary);
}

.step-circle {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--font-size-xs);
  font-weight: 600;
  position: relative;
  z-index: 1;
  background: var(--bg-elevated);
  border: 2px solid var(--border-primary);
  color: var(--text-quaternary);
  transition: all var(--transition-normal);
  flex-shrink: 0;
}

.step-circle svg {
  width: 14px;
  height: 14px;
}

.step-done .step-circle {
  background: var(--text-primary);
  border-color: var(--text-primary);
  color: var(--text-inverse);
}

.step-current .step-circle {
  background: var(--text-primary);
  border-color: var(--text-primary);
  color: var(--text-inverse);
}

.step-error .step-circle {
  background: var(--error-bg);
  border-color: var(--error-text);
  color: var(--error-text);
}

.step-pulse-ring {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 2px solid var(--text-primary);
  transform: translate(-50%, -50%);
  animation: stepPulse 2s ease-out infinite;
  pointer-events: none;
}

@keyframes stepPulse {
  0% {
    transform: translate(-50%, -50%) scale(1);
    opacity: 0.5;
  }
  100% {
    transform: translate(-50%, -50%) scale(1.8);
    opacity: 0;
  }
}

.step-label {
  margin-top: 8px;
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  text-align: center;
  font-weight: 500;
}

.step-done .step-label,
.step-current .step-label {
  color: var(--text-primary);
}

.step-error .step-label {
  color: var(--error-text);
}

/* 成功结果卡片 */
.result-card {
  padding: 28px;
}

.result-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--border-secondary);
}

.result-icon {
  width: 48px;
  height: 48px;
  border-radius: var(--radius-lg);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: var(--bg-tertiary);
  color: var(--text-tertiary);
}

.result-icon svg {
  width: 24px;
  height: 24px;
}

.success-icon {
  background: var(--success-bg);
  color: var(--success-text);
}

.error-icon {
  background: var(--error-bg);
  color: var(--error-text);
}

.warning-icon {
  background: var(--warning-bg);
  color: var(--warning-text);
}

.cancelled-icon {
  background: var(--bg-tertiary);
  color: var(--text-tertiary);
}

.result-header-text {
  flex: 1;
  min-width: 0;
}

.result-title {
  margin: 0 0 4px;
  font-size: var(--font-size-lg);
  font-weight: 700;
  color: var(--text-primary);
}

.result-subtitle {
  margin: 0;
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
}

/* 结果统计 */
.result-stats {
  margin-bottom: 24px;
}

.stat-item {
  margin-bottom: 16px;
}

.stat-item:last-child {
  margin-bottom: 0;
}

.stat-value {
  font-size: var(--font-size-md);
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 4px;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.stat-label {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  font-weight: 500;
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--border-secondary);
}

.stat-item.small .stat-value {
  font-size: var(--font-size-lg);
  font-weight: 700;
}

.stat-item.small .stat-value.num {
  font-variant-numeric: tabular-nums;
}

/* 失败消息 */
.failure-message {
  padding: 16px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  margin-bottom: 20px;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  line-height: 1.7;
}

/* 结果操作按钮 */
.result-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* 404 空态 */
.detail-404 {
  margin-top: 80px;
}

.empty-state {
  text-align: center;
  padding: 60px 20px;
}

.empty-icon {
  width: 56px;
  height: 56px;
  margin: 0 auto 16px;
  color: var(--text-quaternary);
  display: flex;
  align-items: center;
  justify-content: center;
}

.empty-icon svg {
  width: 56px;
  height: 56px;
}

.empty-title {
  margin: 0 0 6px;
  font-size: var(--font-size-md);
  font-weight: 600;
  color: var(--text-primary);
}

.empty-desc {
  margin: 0 0 20px;
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
}

/* ============ 骨架屏 ============ */
.detail-skeleton {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.skeleton-card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-xl);
  padding: 24px;
}

.skeleton-line {
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  height: 18px;
  position: relative;
  overflow: hidden;
}

.title-line {
  width: 70%;
  height: 24px;
  margin-bottom: 14px;
}

.section-title {
  width: 25%;
  height: 16px;
  margin-bottom: 20px;
}

.skeleton-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}

.skeleton-tag {
  width: 64px;
  height: 22px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-full);
  position: relative;
  overflow: hidden;
}

.skeleton-tag.small {
  width: 48px;
}

.skeleton-progress-bar {
  height: 8px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-full);
  margin-top: 20px;
  position: relative;
  overflow: hidden;
}

.skeleton-bar-fill {
  width: 35%;
  height: 100%;
  background: var(--bg-secondary);
  border-radius: var(--radius-full);
  position: relative;
  overflow: hidden;
}

/* 骨架步骤条 */
.skeleton-stepper {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}

.skeleton-step {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
}

.skeleton-circle {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--bg-tertiary);
  position: relative;
  overflow: hidden;
}

.skeleton-step-label {
  width: 32px;
  height: 12px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  margin-top: 8px;
  position: relative;
  overflow: hidden;
}

/* 骨架结果网格 */
.skeleton-result-grid {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.skeleton-result-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.skeleton-result-num {
  width: 50%;
  height: 20px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  position: relative;
  overflow: hidden;
}

.skeleton-result-label {
  width: 30%;
  height: 12px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  position: relative;
  overflow: hidden;
}

/* shimmer 动画 */
.skeleton-line::after,
.skeleton-tag::after,
.skeleton-bar-fill::after,
.skeleton-circle::after,
.skeleton-step-label::after,
.skeleton-result-num::after,
.skeleton-result-label::after {
  content: '';
  position: absolute;
  top: 0;
  left: -100%;
  width: 100%;
  height: 100%;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255, 255, 255, 0.5) 50%,
    transparent 100%
  );
  animation: shimmer 1.5s infinite;
}

@keyframes shimmer {
  100% {
    left: 100%;
  }
}

/* 响应式 */
@media (max-width: 640px) {
  .task-detail-page {
    padding: 0;
  }

  .detail-header {
    padding: 20px;
  }

  .detail-title {
    font-size: var(--font-size-lg);
  }

  .detail-meta {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }

  .result-card {
    padding: 16px;
  }

  .result-meta {
    grid-template-columns: repeat(2, 1fr);
  }

  .result-actions {
    flex-direction: column;
  }

  .result-actions .btn {
    width: 100%;
  }

  .failure-card {
    padding: 16px;
  }

  .steps-timeline {
    padding: 16px;
  }

  .step-item {
    padding-bottom: 16px;
  }

  .detail-content {
    gap: 16px;
  }
}

@media (max-width: 400px) {
  .result-meta {
    grid-template-columns: 1fr;
  }
}
</style>
