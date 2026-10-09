<template>
  <div class="auth-page">
    <!-- 背景装饰 -->
    <div class="bg-decoration">
      <div class="blob blob-1"></div>
      <div class="blob blob-2"></div>
    </div>

    <div class="auth-card-wrap">
      <!-- Logo -->
      <div class="auth-logo" @click="goHome">
        <BrandLogo size="large" />
        <span class="logo-text">纸慧</span>
      </div>

      <!-- 注册卡片 -->
      <div class="auth-card">
        <div class="card-header">
          <h2>创建账号</h2>
          <p class="card-subtitle">加入我们，开启你的学术阅读新体验</p>
        </div>

        <form class="auth-form" @submit.prevent="onSubmit">
          <div class="form-group">
            <label class="form-label">用户名</label>
            <div class="input-wrap">
              <svg class="input-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                <circle cx="8" cy="5.5" r="2.5" />
                <path d="M3 13.5a5 5 0 0110 0" stroke-linecap="round" />
              </svg>
              <input
                v-model="form.username"
                type="text"
                class="form-input"
                placeholder="3–30 位字母、数字、下划线或中文"
                :disabled="loading"
                data-testid="register-username"
              />
            </div>
          </div>

          <div class="form-group">
            <label class="form-label">密码</label>
            <div class="input-wrap">
              <svg class="input-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                <rect x="3.5" y="5.5" width="9" height="7" rx="1.5" />
                <path d="M5.5 5.5V4a2.5 2.5 0 015 0v1.5" stroke-linecap="round" />
                <circle cx="8" cy="9" r="1" />
              </svg>
              <input
                v-model="form.password"
                :type="showPassword ? 'text' : 'password'"
                class="form-input"
                placeholder="8–64 位"
                :disabled="loading"
                data-testid="register-password"
              />
              <button
                type="button"
                class="password-toggle"
                @click="showPassword = !showPassword"
                :disabled="loading"
              >
                <svg v-if="!showPassword" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M1.5 8s3-4.5 6.5-4.5S14.5 8 14.5 8s-3 4.5-6.5 4.5S1.5 8 1.5 8z" stroke-linejoin="round" />
                  <circle cx="8" cy="8" r="2" />
                </svg>
                <svg v-else viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M2 2l12 12" stroke-linecap="round" />
                  <path d="M1.5 8s1.5-2.2 3.5-3.5M14.5 8s-1.5 2.2-3.5 3.5" stroke-linecap="round" />
                  <path d="M8 5.5v.5M8 10v.5" stroke-linecap="round" />
                </svg>
              </button>
            </div>
          </div>

          <div class="form-group">
            <label class="form-label">确认密码</label>
            <div class="input-wrap">
              <svg class="input-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                <rect x="3.5" y="5.5" width="9" height="7" rx="1.5" />
                <path d="M5.5 5.5V4a2.5 2.5 0 015 0v1.5" stroke-linecap="round" />
                <path d="M6 9h4" stroke-linecap="round" />
              </svg>
              <input
                v-model="form.confirm"
                :type="showConfirm ? 'text' : 'password'"
                class="form-input"
                placeholder="再次输入密码"
                :disabled="loading"
                data-testid="register-confirm"
                @keyup.enter="onSubmit"
              />
              <button
                type="button"
                class="password-toggle"
                @click="showConfirm = !showConfirm"
                :disabled="loading"
              >
                <svg v-if="!showConfirm" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M1.5 8s3-4.5 6.5-4.5S14.5 8 14.5 8s-3 4.5-6.5 4.5S1.5 8 1.5 8z" stroke-linejoin="round" />
                  <circle cx="8" cy="8" r="2" />
                </svg>
                <svg v-else viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M2 2l12 12" stroke-linecap="round" />
                  <path d="M1.5 8s1.5-2.2 3.5-3.5M14.5 8s-1.5 2.2-3.5 3.5" stroke-linecap="round" />
                  <path d="M8 5.5v.5M8 10v.5" stroke-linecap="round" />
                </svg>
              </button>
            </div>
          </div>

          <div v-if="error" class="form-error" data-testid="register-error">
            <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5">
              <circle cx="7" cy="7" r="5.5" />
              <path d="M7 4v3M7 8.5v.5" stroke-linecap="round" />
            </svg>
            <span>{{ error }}</span>
          </div>

          <button
            type="submit"
            class="btn btn-primary btn-block"
            :disabled="loading"
            data-testid="register-submit"
          >
            <svg v-if="loading" class="spin-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M8 2.5a5.5 5.5 0 105.5 5.5" stroke-linecap="round" />
            </svg>
            {{ loading ? '注册中...' : '注册' }}
          </button>
        </form>

        <div class="auth-divider">
          <span>已有账号？</span>
        </div>

        <router-link to="/login" class="btn btn-ghost btn-block">
          返回登录
        </router-link>
      </div>

      <p class="auth-footer">注册即表示你同意我们的服务条款与隐私政策</p>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { register } from '../api/auth'
import BrandLogo from '../components/BrandLogo.vue'

const router = useRouter()
const form = reactive({ username: '', password: '', confirm: '' })
const loading = ref(false)
const error = ref('')
const showPassword = ref(false)
const showConfirm = ref(false)

function goHome() {
  router.push('/')
}

const USERNAME_RE = new RegExp(
  '^[A-Za-z0-9_' + String.fromCharCode(0x4e00) + '-' + String.fromCharCode(0x9fff) + ']{3,30}$',
  'u',
)

function validate() {
  const username = form.username.trim()
  if (!USERNAME_RE.test(username)) {
    return '用户名需为 3–30 位字母、数字、下划线或中文'
  }
  if (form.password.length < 8 || form.password.length > 64) {
    return '密码长度需为 8–64 位'
  }
  if (form.password !== form.confirm) {
    return '两次输入的密码不一致'
  }
  return ''
}

async function onSubmit() {
  error.value = validate()
  if (error.value) return
  loading.value = true
  try {
    await register(form.username.trim(), form.password)
    router.push('/login')
  } catch (e) {
    if (e.response?.status === 409) {
      error.value = e.response.data?.detail || '用户名已被占用'
    } else if (e.response?.status === 422) {
      error.value = '输入不符合要求，请检查用户名与密码'
    } else {
      error.value = '服务不可用，请稍后重试'
    }
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  position: relative;
  background: var(--bg-primary);
  overflow: hidden;
}

/* 背景装饰 —— 柔和 blob */
.bg-decoration {
  position: absolute;
  inset: 0;
  pointer-events: none;
  overflow: hidden;
}

.blob {
  position: absolute;
  border-radius: 50%;
  filter: blur(80px);
  opacity: 0.4;
}

.blob-1 {
  width: 400px;
  height: 400px;
  background: var(--bg-tertiary);
  top: -150px;
  left: -100px;
}

.blob-2 {
  width: 350px;
  height: 350px;
  background: var(--bg-secondary);
  bottom: -120px;
  right: -80px;
}

/* 卡片容器 */
.auth-card-wrap {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 400px;
  display: flex;
  flex-direction: column;
  align-items: center;
}

/* Logo */
.auth-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 32px;
  cursor: pointer;
  user-select: none;
  transition: opacity var(--transition-fast);
}

.auth-logo:hover {
  opacity: 0.8;
}

.logo-icon {
  width: 36px;
  height: 36px;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  justify-content: center;
}

.logo-icon svg {
  width: 36px;
  height: 36px;
}

.logo-text {
  font-size: var(--font-size-lg);
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: 0.3px;
}

/* 卡片 */
.auth-card {
  width: 100%;
  background: var(--bg-elevated);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-xl);
  padding: 32px;
  box-shadow: var(--shadow-md);
}

.card-header {
  text-align: center;
  margin-bottom: 28px;
}

.card-header h2 {
  margin: 0 0 6px;
  font-size: var(--font-size-xl);
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.3px;
}

.card-subtitle {
  margin: 0;
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
}

/* 表单 */
.auth-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-label {
  font-size: var(--font-size-sm);
  font-weight: 500;
  color: var(--text-secondary);
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
  padding: 10px 14px 10px 38px;
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

.password-toggle {
  position: absolute;
  right: 8px;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  transition: all var(--transition-fast);
}

.password-toggle:hover:not(:disabled) {
  color: var(--text-primary);
  background: var(--bg-hover);
}

.password-toggle svg {
  width: 15px;
  height: 15px;
}

.password-toggle:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* 错误提示 */
.form-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: var(--error-bg);
  border: 1px solid var(--error-border);
  border-radius: var(--radius-md);
  font-size: var(--font-size-sm);
  color: var(--error-text);
  animation: errorShake 0.3s ease;
}

.form-error svg {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}

@keyframes errorShake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-4px); }
  75% { transform: translateX(4px); }
}

/* 按钮 */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 10px 20px;
  border-radius: var(--radius-md);
  font-size: var(--font-size-sm);
  font-weight: 500;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all var(--transition-fast);
  font-family: inherit;
  text-decoration: none;
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
  opacity: 0.6;
  cursor: not-allowed;
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

.spin-icon {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* 分隔线 */
.auth-divider {
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 20px 0 16px;
}

.auth-divider span {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
}

/* 页脚 */
.auth-footer {
  margin: 24px 0 0;
  font-size: var(--font-size-xs);
  color: var(--text-quaternary);
  text-align: center;
}
</style>
