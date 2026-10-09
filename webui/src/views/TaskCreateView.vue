<template>
  <AppLayout :username="auth.username" @logout="onLogout">
    <section class="task-create-page">
      <!-- 页头 -->
      <div class="page-head">
        <div>
          <h2>提交转写任务</h2>
          <p class="page-subtitle">
            支持 arXiv 论文链接或上传 PDF 文件，任务将在后台自动处理
          </p>
        </div>
      </div>

      <!-- 提示横幅 -->
      <div v-if="banner" class="banner" :class="`banner-${banner.type}`">
        <div class="banner-icon">
          <svg v-if="banner.type === 'warning'" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5">
            <circle cx="9" cy="9" r="7.5" />
            <path d="M9 5v4M9 11v.5" stroke-linecap="round" />
          </svg>
          <svg v-else viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5">
            <circle cx="9" cy="9" r="7.5" />
            <path d="M6 9l2 2 4-4" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </div>
        <div class="banner-content">
          <div class="banner-title">{{ banner.title }}</div>
          <div class="banner-text">
            {{ banner.text }}
            <router-link v-if="banner.to" :to="banner.to" class="banner-link">
              {{ banner.linkText }} →
            </router-link>
          </div>
        </div>
      </div>

      <!-- 主卡片 -->
      <div class="create-card">
        <!-- Tab 切换 -->
        <div class="tab-bar">
          <button
            class="tab-btn"
            :class="{ active: activeTab === 'url' }"
            @click="activeTab = 'url'"
          >
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M7 3H5a3 3 0 000 6h2M9 13h2a3 3 0 000-6H9" stroke-linecap="round" />
            </svg>
            arXiv 链接
          </button>
          <button
            class="tab-btn"
            :class="{ active: activeTab === 'upload' }"
            @click="activeTab = 'upload'"
          >
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M8 2v6M5.5 5.5L8 2l2.5 3.5" stroke-linecap="round" stroke-linejoin="round" />
              <path d="M2.5 10.5v1h9v-1" stroke-linecap="round" />
            </svg>
            上传 PDF
          </button>
        </div>

        <!-- arXiv 链接面板 -->
        <div v-show="activeTab === 'url'" class="tab-panel">
          <div class="form-group">
            <label class="form-label">论文链接</label>
            <div class="input-wrap">
              <svg class="input-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M7 3H5a3 3 0 000 6h2M9 13h2a3 3 0 000-6H9" stroke-linecap="round" />
              </svg>
              <input
                v-model="url"
                type="text"
                class="form-input"
                placeholder="https://arxiv.org/abs/1706.03762"
                :disabled="submitting"
                @keyup.enter="onSubmitUrl"
              />
            </div>
            <p v-if="urlError" class="field-error">
              <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5">
                <circle cx="6" cy="6" r="5" />
                <path d="M6 3.5v3M6 8v.5" stroke-linecap="round" />
              </svg>
              {{ urlError }}
            </p>
            <p class="form-hint">
              支持 arxiv.org/abs/ 和 arxiv.org/pdf/ 格式的链接
            </p>
          </div>

          <button
            class="btn btn-primary btn-block"
            :disabled="submitting || !url.trim()"
            @click="onSubmitUrl"
          >
            <svg v-if="submitting" class="spin-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M8 2.5a5.5 5.5 0 105.5 5.5" stroke-linecap="round" />
            </svg>
            {{ submitting ? '提交中...' : '提交任务' }}
          </button>
        </div>

        <!-- 上传 PDF 面板 -->
        <div v-show="activeTab === 'upload'" class="tab-panel">
          <div
            class="upload-area"
            :class="{ 'is-dragover': isDragover, 'has-file': pickedFile }"
            @dragover.prevent="isDragover = true"
            @dragleave.prevent="isDragover = false"
            @drop.prevent="onDrop"
            @click="triggerFileInput"
          >
            <input
              ref="fileInputRef"
              type="file"
              accept=".pdf,application/pdf"
              class="file-input"
              @change="onFileInput"
            />

            <div v-if="!pickedFile" class="upload-placeholder">
              <div class="upload-icon">
                <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M16 6v12M11 11l5-5 5 5" stroke-linecap="round" stroke-linejoin="round" />
                  <path d="M6 22v2a2 2 0 002 2h16a2 2 0 002-2v-2" stroke-linecap="round" />
                </svg>
              </div>
              <div class="upload-title">
                {{ isDragover ? '松开鼠标上传文件' : '拖拽 PDF 文件到此处' }}
              </div>
              <div class="upload-hint">
                或 <span class="upload-link">点击选择文件</span> · 最大 50MB
              </div>
            </div>

            <div v-else class="upload-file-info">
              <div class="file-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" stroke-linejoin="round" />
                  <path d="M14 2v6h6" stroke-linejoin="round" />
                </svg>
              </div>
              <div class="file-info">
                <div class="file-name">{{ pickedFile.name }}</div>
                <div class="file-size">{{ formatSize(pickedFile.size) }}</div>
              </div>
              <button class="file-remove" @click.stop="clearFile">
                <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M3 3l8 8M11 3l-8 8" stroke-linecap="round" />
                </svg>
              </button>
            </div>
          </div>

          <p v-if="fileError" class="field-error">
            <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5">
              <circle cx="6" cy="6" r="5" />
              <path d="M6 3.5v3M6 8v.5" stroke-linecap="round" />
            </svg>
            {{ fileError }}
          </p>

          <button
            class="btn btn-primary btn-block"
            :disabled="submitting || !pickedFile"
            @click="onSubmitUpload"
          >
            <svg v-if="submitting" class="spin-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M8 2.5a5.5 5.5 0 105.5 5.5" stroke-linecap="round" />
            </svg>
            {{ submitting ? '提交中...' : '提交任务' }}
          </button>
        </div>
      </div>

      <!-- 说明卡片 -->
      <div class="info-card">
        <div class="info-item">
          <div class="info-icon">
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
              <circle cx="8" cy="8" r="6.5" />
              <path d="M8 5v3M8 10.5v.5" stroke-linecap="round" />
            </svg>
          </div>
          <div class="info-content">
            <div class="info-title">处理流程</div>
            <div class="info-desc">六阶段智能处理：摄取 → 解析 → 结构化 → 规划 → 转写 → 成稿</div>
          </div>
        </div>
        <div class="info-item">
          <div class="info-icon">
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M8 2v3M8 11v3M2 8h3M11 8h3" stroke-linecap="round" />
              <circle cx="8" cy="8" r="3" />
            </svg>
          </div>
          <div class="info-content">
            <div class="info-title">实时进度</div>
            <div class="info-desc">提交后可在任务列表实时查看处理进度与阶段状态</div>
          </div>
        </div>
        <div class="info-item">
          <div class="info-icon">
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M12 5.5l-3.5 3.5L7.5 8 5 10.5 2.5 8" stroke-linecap="round" stroke-linejoin="round" />
              <path d="M12 2.5a4 4 0 011.5 3 4 4 0 01-1.5 3M4 13.5a4 4 0 01-1.5-3 4 4 0 011.5-3" stroke-linecap="round" />
            </svg>
          </div>
          <div class="info-content">
            <div class="info-title">隐私安全</div>
            <div class="info-desc">论文内容仅用于本次转写，不存储、不分享</div>
          </div>
        </div>
      </div>
    </section>
  </AppLayout>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import AppLayout from '../layouts/AppLayout.vue'
import { useAuthStore } from '../stores/auth'
import { createFromUrl, createUpload } from '../api/tasks'

const MAX_MB = 50
const MAX_BYTES = MAX_MB * 1024 * 1024

const router = useRouter()
const auth = useAuthStore()
const message = useMessage()

const activeTab = ref('url')
const url = ref('')
const urlError = ref('')
const pickedFile = ref(null)
const fileError = ref('')
const submitting = ref(false)
const banner = ref(null)
const isDragover = ref(false)
const fileInputRef = ref(null)

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function looksLikeArxiv(text) {
  const v = text.trim()
  return /^https?:\/\//i.test(v) && /arxiv\.org\/(abs|pdf)\//i.test(v)
}

function validateFile(file) {
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    fileError.value = '仅支持 .pdf 文件'
    return false
  }
  if (file.size > MAX_BYTES) {
    fileError.value = `文件超过 ${MAX_MB}MB 上限（当前 ${formatSize(file.size)}），请压缩后再上传`
    return false
  }
  return true
}

function triggerFileInput() {
  if (submitting.value) return
  fileInputRef.value?.click()
}

function onFileInput(e) {
  const file = e.target.files?.[0]
  if (!file) return
  handleFile(file)
}

function onDrop(e) {
  isDragover.value = false
  const file = e.dataTransfer?.files?.[0]
  if (!file) return
  handleFile(file)
}

function handleFile(file) {
  banner.value = null
  fileError.value = ''
  if (validateFile(file)) {
    pickedFile.value = file
  } else {
    pickedFile.value = null
  }
}

function clearFile() {
  pickedFile.value = null
  fileError.value = ''
  if (fileInputRef.value) fileInputRef.value.value = ''
}

function handleError(err) {
  const status = err.response?.status
  const code = err.response?.data?.code
  const detail = err.response?.data?.detail
  if (status === 400 && code === 'credentials_required') {
    banner.value = {
      type: 'warning',
      title: '需要先配置密钥',
      text: '真实模式下，请先完成 MinerU 与 DeepSeek 密钥配置后再提交任务。',
      to: '/credentials',
      linkText: '去密钥设置',
    }
    return
  }
  if (status === 409 && code === 'active_task_exists') {
    banner.value = {
      type: 'warning',
      title: '已有进行中的任务',
      text: '每个账号同一时间只能运行一个任务，请等待当前任务结束。',
      to: '/tasks',
      linkText: '查看任务列表',
    }
    return
  }
  if (status === 422) {
    message.error(typeof detail === 'string' ? detail : '提交内容未通过校验，请检查输入')
    return
  }
  if (status === 413 && code === 'file_too_large') {
    fileError.value = detail || `文件超过 ${MAX_MB}MB 上限`
    return
  }
  message.error('提交失败，请稍后重试')
}

async function afterCreated(taskId) {
  message.success('任务已提交，开始后台处理')
  await router.push('/tasks')
}

async function onSubmitUrl() {
  banner.value = null
  urlError.value = ''
  const value = url.value.trim()
  if (!value) {
    urlError.value = '请输入 arXiv 论文链接'
    return
  }
  if (!looksLikeArxiv(value)) {
    urlError.value = '链接格式不正确，示例：https://arxiv.org/abs/1706.03762'
    return
  }
  submitting.value = true
  try {
    const { data } = await createFromUrl(value)
    await afterCreated(data.id)
  } catch (err) {
    handleError(err)
  } finally {
    submitting.value = false
  }
}

async function onSubmitUpload() {
  banner.value = null
  if (!pickedFile.value) {
    fileError.value = '请先选择 PDF 文件'
    return
  }
  submitting.value = true
  try {
    const { data } = await createUpload(pickedFile.value)
    await afterCreated(data.id)
  } catch (err) {
    handleError(err)
  } finally {
    submitting.value = false
  }
}

async function onLogout() {
  await auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.task-create-page {
  max-width: 640px;
  margin: 0 auto;
}

/* 页头 */
.page-head {
  margin-bottom: 24px;
}

.page-head h2 {
  margin: 0 0 6px;
  font-size: var(--font-size-2xl);
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.3px;
}

.page-subtitle {
  margin: 0;
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  line-height: 1.6;
}

/* 横幅 */
.banner {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 16px;
  border-radius: var(--radius-lg);
  margin-bottom: 20px;
  border: 1px solid var(--border-primary);
}

.banner-warning {
  background: var(--warning-bg);
  border-color: var(--warning-border);
  color: var(--warning-text);
}

.banner-success {
  background: var(--success-bg);
  border-color: var(--success-border);
  color: var(--success-text);
}

.banner-icon {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  margin-top: 1px;
}

.banner-icon svg {
  width: 20px;
  height: 20px;
}

.banner-content {
  flex: 1;
  min-width: 0;
}

.banner-title {
  font-size: var(--font-size-sm);
  font-weight: 600;
  margin-bottom: 2px;
}

.banner-text {
  font-size: var(--font-size-sm);
  opacity: 0.9;
  line-height: 1.6;
}

.banner-link {
  font-weight: 600;
  text-decoration: none;
  margin-left: 4px;
}

/* 主卡片 */
.create-card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-xl);
  padding: 24px;
  margin-bottom: 20px;
}

/* Tab 切换 */
.tab-bar {
  display: flex;
  gap: 4px;
  padding: 4px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  margin-bottom: 24px;
}

.tab-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 16px;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  font-weight: 500;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: all var(--transition-fast);
  font-family: inherit;
}

.tab-btn svg {
  width: 16px;
  height: 16px;
}

.tab-btn:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary);
}

.tab-btn.active {
  background: var(--bg-elevated);
  color: var(--text-primary);
  box-shadow: var(--shadow-sm);
}

/* Tab 面板 */
.tab-panel {
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

/* 表单 */
.form-group {
  margin-bottom: 20px;
}

.form-label {
  display: block;
  font-size: var(--font-size-sm);
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 8px;
}

.input-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.input-icon {
  position: absolute;
  left: 12px;
  width: 16px;
  height: 16px;
  color: var(--text-tertiary);
  pointer-events: none;
}

.form-input {
  width: 100%;
  padding: 11px 14px 11px 38px;
  font-size: var(--font-size-sm);
  font-family: inherit;
  color: var(--text-primary);
  background: var(--bg-primary);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-md);
  transition: all var(--transition-fast);
  outline: none;
}

.form-input::placeholder {
  color: var(--text-quaternary);
}

.form-input:hover:not(:disabled) {
  border-color: var(--border-strong);
}

.form-input:focus {
  border-color: var(--border-focus);
  box-shadow: 0 0 0 3px var(--bg-tertiary);
}

.form-input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.form-hint {
  margin: 8px 0 0;
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
}

.field-error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 8px 0 0;
  font-size: var(--font-size-xs);
  color: var(--error-text);
}

.field-error svg {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
}

/* 按钮 */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 11px 20px;
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

.btn-block {
  width: 100%;
}

.btn-primary {
  background: var(--text-primary);
  color: var(--text-inverse);
  border-color: var(--text-primary);
}

.btn-primary:hover:not(:disabled) {
  background: var(--text-secondary);
  border-color: var(--text-secondary);
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.spin-icon {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* 上传区域 */
.upload-area {
  position: relative;
  border: 2px dashed var(--border-primary);
  border-radius: var(--radius-lg);
  padding: 40px 24px;
  text-align: center;
  cursor: pointer;
  transition: all var(--transition-fast);
  background: var(--bg-primary);
  margin-bottom: 20px;
}

.upload-area:hover {
  border-color: var(--border-strong);
  background: var(--bg-secondary);
}

.upload-area.is-dragover {
  border-color: var(--text-primary);
  background: var(--bg-secondary);
}

.upload-area.has-file {
  cursor: default;
  background: var(--bg-secondary);
}

.file-input {
  display: none;
}

.upload-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.upload-icon {
  width: 48px;
  height: 48px;
  color: var(--text-tertiary);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.upload-icon svg {
  width: 48px;
  height: 48px;
}

.upload-title {
  font-size: var(--font-size-base);
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 6px;
}

.upload-hint {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
}

.upload-link {
  color: var(--text-primary);
  font-weight: 500;
  text-decoration: underline;
  text-underline-offset: 2px;
}

/* 已选文件 */
.upload-file-info {
  display: flex;
  align-items: center;
  gap: 12px;
  text-align: left;
}

.file-icon {
  width: 40px;
  height: 40px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-tertiary);
  border-radius: var(--radius-md);
  color: var(--text-secondary);
}

.file-icon svg {
  width: 20px;
  height: 20px;
}

.file-info {
  flex: 1;
  min-width: 0;
}

.file-name {
  font-size: var(--font-size-sm);
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-bottom: 2px;
}

.file-size {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
}

.file-remove {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  border-radius: var(--radius-sm);
  transition: all var(--transition-fast);
  flex-shrink: 0;
}

.file-remove:hover {
  background: var(--error-bg);
  color: var(--error-text);
}

.file-remove svg {
  width: 14px;
  height: 14px;
}

/* 说明卡片 */
.info-card {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.info-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 16px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-lg);
}

.info-icon {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-secondary);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
}

.info-icon svg {
  width: 16px;
  height: 16px;
}

.info-content {
  flex: 1;
  min-width: 0;
}

.info-title {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.info-desc {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  line-height: 1.6;
}

/* 响应式 */
@media (max-width: 640px) {
  .page-head h2 {
    font-size: var(--font-size-xl);
  }

  .create-card {
    padding: 16px;
  }

  .tab-btn {
    padding: 8px 12px;
    font-size: var(--font-size-xs);
  }

  .upload-area {
    padding: 32px 16px;
  }

  .upload-icon {
    width: 40px;
    height: 40px;
  }

  .upload-icon svg {
    width: 40px;
    height: 40px;
  }

  .info-card {
    grid-template-columns: 1fr;
  }
}
</style>
