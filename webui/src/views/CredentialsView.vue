<template>
  <AppLayout :username="auth.username" @logout="onLogout">
    <section class="credentials-page">
      <!-- 页面标题 -->
      <div class="page-header">
        <div>
          <h2 class="page-title">密钥设置</h2>
          <p class="page-desc">
            密钥经 Fernet 加密后保存，页面仅显示末 4 位掩码；用于提交论文转写任务。
          </p>
        </div>
        <div v-if="loading" class="skeleton-title"></div>
      </div>

      <!-- 加载骨架屏 -->
      <div v-if="loading" class="cred-list">
        <div v-for="i in 2" :key="i" class="cred-card skeleton">
          <div class="cred-card-header">
            <div class="skeleton-text skeleton-title"></div>
            <div class="skeleton-tag"></div>
          </div>
          <div class="skeleton-input"></div>
          <div class="skeleton-btn-row">
            <div class="skeleton-btn skeleton-btn-primary"></div>
            <div class="skeleton-btn skeleton-btn-ghost"></div>
          </div>
        </div>
      </div>

      <!-- 密钥卡片列表 -->
      <div v-else class="cred-list">
        <div v-for="svc in services" :key="svc.key" class="cred-card">
          <div class="cred-card-header">
            <div class="cred-title">
              <span class="cred-icon" :class="svc.key">
                <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8">
                  <path v-if="svc.key === 'mineru'" d="M3 5h14M3 10h14M3 15h10" stroke-linecap="round"/>
                  <path v-else d="M10 2v3M4 5l2 2M16 5l-2 2M5 10a5 5 0 0 0 10 0M4 17h12" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
              </span>
              <span>{{ svc.label }}</span>
            </div>
            <span
              class="status-badge"
              :class="statusOf(svc.key)?.configured ? 'status-badge--success' : 'status-badge--warning'"
            >
              <span class="status-dot"></span>
              {{ statusOf(svc.key)?.configured ? '已配置 ' + statusOf(svc.key).mask : '未配置' }}
            </span>
          </div>

          <div class="cred-body">
            <div class="form-group">
              <label class="form-label">密钥</label>
              <div class="input-wrap" :class="{ 'is-focus': focusKey === svc.key, 'is-error': errors[svc.key] }">
                <span class="input-icon">
                  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                    <rect x="3" y="5" width="10" height="9" rx="1.5"/>
                    <path d="M5 5V4a3 3 0 0 1 6 0v1" stroke-linecap="round"/>
                  </svg>
                </span>
                <input
                  :type="showPassword[svc.key] ? 'text' : 'password'"
                  v-model="drafts[svc.key]"
                  :placeholder="statusOf(svc.key)?.configured ? '输入新密钥以覆盖' : '粘贴密钥后保存'"
                  :disabled="saving === svc.key"
                  @focus="focusKey = svc.key"
                  @blur="focusKey = null"
                />
                <button
                  v-if="drafts[svc.key]"
                  type="button"
                  class="input-toggle"
                  @click="showPassword[svc.key] = !showPassword[svc.key]"
                  :aria-label="showPassword[svc.key] ? '隐藏' : '显示'"
                >
                  <svg v-if="showPassword[svc.key]" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path d="M1 8s3.5-5 7-5 7 5 7 5-3.5 5-7 5S1 8 1 8z" stroke-linecap="round" stroke-linejoin="round"/>
                    <circle cx="8" cy="8" r="2"/>
                  </svg>
                  <svg v-else viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path d="M2 2l12 12M6.5 6.5C5.5 7.2 5 8 5 8s1.5 2.5 3 2.5c.4 0 .7-.1 1-.2M9.5 5.5C10.5 6.2 11 7 11 7s-.5 1-1.5 2M1 8s3.5-5 7-5c1.2 0 2.3.3 3.3.8M15 8s-2 3-5 4" stroke-linecap="round" stroke-linejoin="round"/>
                  </svg>
                </button>
              </div>
              <p v-if="errors[svc.key]" class="field-error">{{ errors[svc.key] }}</p>
            </div>

            <div class="btn-row">
              <button
                class="btn btn-primary btn-sm"
                :class="{ 'is-loading': saving === svc.key }"
                :disabled="saving === svc.key"
                @click="onSave(svc.key)"
              >
                <span v-if="saving === svc.key" class="btn-spinner"></span>
                {{ statusOf(svc.key)?.configured ? '更新密钥' : '保存密钥' }}
              </button>
              <button
                class="btn btn-ghost btn-sm btn-warn"
                :disabled="!statusOf(svc.key)?.configured || saving === svc.key"
                @click="onDelete(svc)"
              >
                删除
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 说明卡片 -->
      <div class="info-note">
        <div class="info-note-icon">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
            <circle cx="8" cy="8" r="6.5"/>
            <path d="M8 5v.1M8 8v4" stroke-linecap="round"/>
          </svg>
        </div>
        <div class="info-note-body">
          <p class="info-note-title">关于密钥安全</p>
          <p class="info-note-text">
            你的 API 密钥使用 Fernet 对称加密算法加密存储，主密钥由系统管理员在部署时配置。
            系统不会向任何第三方泄露你的密钥，仅在调用解析和转写服务时使用。
          </p>
        </div>
      </div>

      <!-- 删除确认弹框 -->
      <div v-if="deleteDialog.visible" class="modal-backdrop" @click.self="deleteDialog.visible = false">
        <div class="modal-card">
          <div class="modal-icon modal-icon--warn">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8">
              <path d="M10 3L2 17h16L10 3z" stroke-linejoin="round"/>
              <path d="M10 8v4M10 14v.5" stroke-linecap="round"/>
            </svg>
          </div>
          <h3 class="modal-title">删除{{ deleteDialog.svc?.label }}密钥</h3>
          <p class="modal-desc">删除后，提交真实转写任务前需要重新配置。确定删除吗？</p>
          <div class="modal-actions">
            <button class="btn btn-ghost btn-sm" @click="deleteDialog.visible = false">取消</button>
            <button class="btn btn-warn btn-sm" @click="confirmDelete">确认删除</button>
          </div>
        </div>
      </div>
    </section>
  </AppLayout>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppLayout from '../layouts/AppLayout.vue'
import { useAuthStore } from '../stores/auth'
import { deleteCredential, listCredentials, saveCredential } from '../api/credentials'

const services = [
  { key: 'mineru', label: 'MinerU · 论文解析' },
  { key: 'deepseek', label: 'DeepSeek · 大模型转写' },
]

const router = useRouter()
const auth = useAuthStore()

const loading = ref(false)
const saving = ref('')
const focusKey = ref(null)
const statuses = ref([])
const drafts = reactive({ mineru: '', deepseek: '' })
const errors = reactive({ mineru: '', deepseek: '' })
const showPassword = reactive({ mineru: false, deepseek: false })
const deleteDialog = reactive({ visible: false, svc: null, key: '' })

function statusOf(key) {
  return statuses.value.find((s) => s.service === key)
}

async function load() {
  loading.value = true
  try {
    const { data } = await listCredentials()
    statuses.value = data.credentials
  } catch (e) {
    if (e.response) console.error('加载密钥状态失败')
  } finally {
    loading.value = false
  }
}

async function onSave(key) {
  errors[key] = ''
  const secret = drafts[key].trim()
  if (!secret) {
    errors[key] = '密钥不能为空'
    return
  }
  if (secret.length > 200) {
    errors[key] = '密钥长度不能超过 200 个字符'
    return
  }
  saving.value = key
  try {
    await saveCredential(key, secret)
    drafts[key] = ''
    showPassword[key] = false
    await load()
  } catch (e) {
    if (e.response?.status === 422) {
      errors[key] = '密钥不能为空，长度需在 1-200 个字符之间'
    } else {
      errors[key] = '保存失败，请稍后重试'
    }
  } finally {
    saving.value = ''
  }
}

function onDelete(svc) {
  deleteDialog.svc = svc
  deleteDialog.key = svc.key
  deleteDialog.visible = true
}

async function confirmDelete() {
  try {
    await deleteCredential(deleteDialog.key)
    deleteDialog.visible = false
    await load()
  } catch (e) {
    console.error('删除失败')
  }
}

async function onLogout() {
  await auth.logout()
  router.push('/login')
}

onMounted(() => {
  if (!auth.user && auth.isAuthenticated) auth.fetchMe().catch(() => {})
  load()
})
</script>

<style scoped>
.credentials-page {
  max-width: 680px;
  margin: 0 auto;
}

/* 页面标题 */
.page-header {
  margin-bottom: 24px;
}
.page-title {
  font-size: 22px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 6px;
  letter-spacing: -0.01em;
}
.page-desc {
  font-size: 14px;
  color: var(--text-tertiary);
  margin: 0;
  line-height: 1.6;
}

/* 卡片列表 */
.cred-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-bottom: 24px;
}

.cred-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 20px 24px;
  transition: border-color var(--transition-fast) ease;
}
.cred-card:hover {
  border-color: var(--border-strong);
}

.cred-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.cred-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.cred-icon {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
  background: var(--bg-secondary);
}
.cred-icon svg {
  width: 18px;
  height: 18px;
}
.cred-icon.mineru {
  color: var(--text-secondary);
}
.cred-icon.deepseek {
  color: var(--text-secondary);
}

/* 状态徽章 */
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
}
.status-badge--success {
  background: color-mix(in srgb, var(--success-color) 12%, transparent);
  color: var(--success-color);
}
.status-badge--warning {
  background: color-mix(in srgb, var(--warning-color) 12%, transparent);
  color: var(--warning-color);
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

/* 表单 */
.cred-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
}

.input-wrap {
  display: flex;
  align-items: center;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 0 12px;
  height: 42px;
  transition: all var(--transition-fast) ease;
}
.input-wrap:hover {
  border-color: var(--border-strong);
}
.input-wrap.is-focus {
  border-color: var(--accent-color);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent-color) 12%, transparent);
}
.input-wrap.is-error {
  border-color: var(--error-color);
}
.input-wrap.is-error.is-focus {
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--error-color) 12%, transparent);
}

.input-icon {
  display: flex;
  align-items: center;
  color: var(--text-tertiary);
  margin-right: 8px;
  flex-shrink: 0;
}
.input-icon svg {
  width: 16px;
  height: 16px;
}

.input-wrap input {
  flex: 1;
  height: 100%;
  border: none;
  outline: none;
  background: transparent;
  font-size: 14px;
  color: var(--text-primary);
  font-family: inherit;
}
.input-wrap input::placeholder {
  color: var(--text-placeholder);
}
.input-wrap input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.input-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  border-radius: var(--radius-sm);
  transition: all var(--transition-fast) ease;
  flex-shrink: 0;
}
.input-toggle:hover {
  color: var(--text-secondary);
  background: var(--bg-secondary);
}
.input-toggle svg {
  width: 16px;
  height: 16px;
}

.field-error {
  color: var(--error-color);
  font-size: 12px;
  margin: 0;
  line-height: 1.4;
}

/* 按钮行 */
.btn-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* 通用按钮 */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 36px;
  padding: 0 18px;
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all var(--transition-fast) ease;
  white-space: nowrap;
}
.btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.btn-sm {
  height: 34px;
  padding: 0 16px;
  font-size: 13px;
}
.btn-primary {
  background: var(--text-primary);
  color: var(--bg-primary);
  border-color: var(--text-primary);
}
.btn-primary:hover:not(:disabled) {
  opacity: 0.9;
}
.btn-ghost {
  background: transparent;
  color: var(--text-secondary);
  border-color: var(--border);
}
.btn-ghost:hover:not(:disabled) {
  background: var(--bg-secondary);
  color: var(--text-primary);
}
.btn-warn {
  color: var(--error-color);
}
.btn-warn:hover:not(:disabled) {
  background: color-mix(in srgb, var(--error-color) 8%, transparent);
  border-color: color-mix(in srgb, var(--error-color) 30%, transparent);
}

.btn-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* 说明卡片 */
.info-note {
  display: flex;
  gap: 14px;
  padding: 18px 20px;
  background: var(--bg-secondary);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border);
}
.info-note-icon {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: var(--bg-card);
  color: var(--text-tertiary);
  border: 1px solid var(--border);
}
.info-note-icon svg {
  width: 18px;
  height: 18px;
}
.info-note-body {
  flex: 1;
  min-width: 0;
}
.info-note-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 4px;
}
.info-note-text {
  font-size: 13px;
  color: var(--text-tertiary);
  margin: 0;
  line-height: 1.6;
}

/* 骨架屏 */
.skeleton {
  position: relative;
  overflow: hidden;
}
.skeleton::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(
    90deg,
    transparent 0%,
    color-mix(in srgb, var(--text-primary) 4%, transparent) 50%,
    transparent 100%
  );
  animation: shimmer 1.5s ease-in-out infinite;
}
@keyframes shimmer {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(100%); }
}
.skeleton-title {
  height: 20px;
  width: 120px;
  background: var(--bg-secondary);
  border-radius: 4px;
}
.skeleton-text {
  height: 16px;
  background: var(--bg-secondary);
  border-radius: 4px;
}
.skeleton-text.skeleton-title {
  height: 18px;
  width: 140px;
}
.skeleton-tag {
  height: 22px;
  width: 72px;
  background: var(--bg-secondary);
  border-radius: 999px;
}
.skeleton-input {
  height: 42px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
}
.skeleton-btn-row {
  display: flex;
  gap: 10px;
}
.skeleton-btn {
  height: 34px;
  border-radius: var(--radius-md);
  background: var(--bg-secondary);
}
.skeleton-btn-primary {
  width: 100px;
}
.skeleton-btn-ghost {
  width: 70px;
}

/* 弹框 */
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: color-mix(in srgb, #000 50%, transparent);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal);
  animation: fadeIn 0.15s ease;
}
.modal-card {
  width: 400px;
  max-width: calc(100vw - 32px);
  background: var(--bg-card);
  border-radius: var(--radius-xl);
  padding: 28px;
  text-align: center;
  animation: modalIn 0.2s ease;
}
.modal-icon {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 16px;
}
.modal-icon--warn {
  background: color-mix(in srgb, var(--warning-color) 12%, transparent);
  color: var(--warning-color);
}
.modal-icon svg {
  width: 24px;
  height: 24px;
}
.modal-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 8px;
}
.modal-desc {
  font-size: 14px;
  color: var(--text-tertiary);
  margin: 0 0 20px;
  line-height: 1.6;
}
.modal-actions {
  display: flex;
  gap: 10px;
  justify-content: center;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes modalIn {
  from { opacity: 0; transform: translateY(8px) scale(0.97); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

/* 响应式 */
@media (max-width: 640px) {
  .cred-card {
    padding: 16px 18px;
  }
  .page-title {
    font-size: 20px;
  }
  .modal-card {
    padding: 24px 20px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .btn, .input-wrap, .cred-card, .status-badge, .skeleton::after,
  .modal-backdrop, .modal-card {
    transition: none;
    animation: none;
  }
}
</style>
