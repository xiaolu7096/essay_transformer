<template>
  <div class="landing-page">
    <!-- 导航栏 -->
    <header class="landing-nav">
      <div class="nav-inner">
        <div class="nav-brand">
          <BrandLogo />
          <span class="nav-brand-text">纸慧</span>
        </div>
        <div class="nav-actions">
          <div class="status-pill" :class="statusClass" data-testid="backend-status">
            <span class="status-dot"></span>
            <span class="status-text">{{ statusText }}</span>
          </div>
          <router-link v-if="!auth.isAuthenticated" to="/login" class="btn btn-ghost btn-sm">登录</router-link>
          <router-link v-if="!auth.isAuthenticated" to="/register" class="btn btn-primary btn-sm">注册</router-link>
          <template v-else>
            <router-link to="/home" class="btn btn-primary btn-sm">进入应用</router-link>
            <button class="btn btn-ghost btn-sm" @click="handleLogout">退出</button>
          </template>
        </div>
      </div>
    </header>

    <main>
      <!-- Hero Section -->
      <section class="hero-section">
        <div class="hero-inner">
          <div class="hero-content">
            <div class="hero-badge">
              <span class="badge-dot"></span>
              AI 驱动的学术阅读工具
            </div>
            <h1 class="hero-title">
              把学术论文<br />
              <span class="highlight">变成易读的中文讲义</span>
            </h1>
            <p class="hero-desc">
              上传 PDF 或输入 arXiv 链接，智能解析论文结构，
              用通俗易懂的语言重写复杂概念，让你的阅读效率提升 10 倍。
            </p>
            <div class="hero-actions">
              <router-link v-if="!auth.isAuthenticated" to="/register" class="btn btn-primary btn-lg">
                免费开始使用
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M6 3l5 5-5 5" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </router-link>
              <router-link v-if="!auth.isAuthenticated" to="/login" class="btn btn-ghost btn-lg">
                已有账号，去登录
              </router-link>
              <router-link v-else to="/home" class="btn btn-primary btn-lg">
                进入应用
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M6 3l5 5-5 5" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </router-link>
            </div>
            <div class="hero-stats">
              <div class="stat-item">
                <div class="stat-num">10x</div>
                <div class="stat-label">阅读效率</div>
              </div>
              <div class="stat-divider"></div>
              <div class="stat-item">
                <div class="stat-num">6 阶段</div>
                <div class="stat-label">智能处理</div>
              </div>
              <div class="stat-divider"></div>
              <div class="stat-item">
                <div class="stat-num">100%</div>
                <div class="stat-label">中文输出</div>
              </div>
            </div>
          </div>

          <!-- Hero 右侧示意图 -->
          <div class="hero-visual">
            <div class="visual-card">
              <div class="visual-header">
                <div class="visual-dots">
                  <span></span><span></span><span></span>
                </div>
                <div class="visual-title">论文转写预览</div>
              </div>
              <div class="visual-body">
                <div class="visual-line long"></div>
                <div class="visual-line short"></div>
                <div class="visual-line medium"></div>
                <div class="visual-block">
                  <div class="block-icon">
                    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5">
                      <path d="M4 10l4 4 8-8" stroke-linecap="round" stroke-linejoin="round" />
                    </svg>
                  </div>
                  <div class="block-content">
                    <div class="block-title">核心观点</div>
                    <div class="block-desc">用通俗语言解释论文核心思想</div>
                  </div>
                </div>
                <div class="visual-line long"></div>
                <div class="visual-line medium"></div>
                <div class="visual-line short"></div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- Feature Section -->
      <section class="feature-section">
        <div class="section-header">
          <span class="section-tag">核心功能</span>
          <h2 class="section-title">专为学术阅读优化的每一个细节</h2>
          <p class="section-desc">从论文解析到知识重述，每一环节都经过精心设计</p>
        </div>

        <div class="feature-grid">
          <div class="feature-card">
            <div class="feature-icon icon-purple">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" stroke-linejoin="round" />
                <path d="M14 2v6h6" stroke-linejoin="round" />
                <path d="M8 13h8M8 17h5" stroke-linecap="round" />
              </svg>
            </div>
            <h3 class="feature-title">智能结构解析</h3>
            <p class="feature-desc">
              自动识别论文标题、摘要、章节、图表、参考文献，保持原始结构的同时优化阅读体验。
            </p>
          </div>

          <div class="feature-card">
            <div class="feature-icon icon-green">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M4 7V5a2 2 0 012-2h2M16 3h2a2 2 0 012 2v2M20 17v2a2 2 0 01-2 2h-2M8 21H6a2 2 0 01-2-2v-2" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            </div>
            <h3 class="feature-title">通俗化重写</h3>
            <p class="feature-desc">
              将晦涩的学术语言转化为通俗易懂的中文表达，保留核心观点的同时降低理解门槛。
            </p>
          </div>

          <div class="feature-card">
            <div class="feature-icon icon-orange">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M12 2l2.4 7.4H22l-6.2 4.5 2.4 7.4L12 16.8 5.8 21.3l2.4-7.4L2 9.4h7.6L12 2z" stroke-linejoin="round" />
              </svg>
            </div>
            <h3 class="feature-title">重点高亮</h3>
            <p class="feature-desc">
              自动识别并突出关键结论、实验结果和核心方法，让你一眼抓住论文精华。
            </p>
          </div>

          <div class="feature-card">
            <div class="feature-icon icon-blue">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" stroke-linejoin="round" />
                <path d="M8 10h8M8 14h5" stroke-linecap="round" />
              </svg>
            </div>
            <h3 class="feature-title">沉浸式阅读</h3>
            <p class="feature-desc">
              护眼配色、字号调节、进度追踪，为长时间阅读提供最舒适的体验。
            </p>
          </div>

          <div class="feature-card">
            <div class="feature-icon icon-pink">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <rect x="3" y="4" width="18" height="16" rx="2" />
                <path d="M7 8h10M7 12h6M7 16h8" stroke-linecap="round" />
              </svg>
            </div>
            <h3 class="feature-title">参考文献折叠</h3>
            <p class="feature-desc">
              智能折叠参考文献与图表数据，保持主文流畅，需要时一键展开查阅。
            </p>
          </div>

          <div class="feature-card">
            <div class="feature-icon icon-teal">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" stroke-linejoin="round" />
                <path d="M9 12l2 2 4-4" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </div>
            <h3 class="feature-title">安全私密</h3>
            <p class="feature-desc">
              你的论文内容仅用于本次转写，不存储、不分享，保护你的学术隐私。
            </p>
          </div>
        </div>
      </section>

      <!-- How it works -->
      <section class="workflow-section">
        <div class="section-header">
          <span class="section-tag">使用流程</span>
          <h2 class="section-title">三步开启高效阅读</h2>
          <p class="section-desc">简单易用，几分钟即可完成一篇论文的转写</p>
        </div>

        <div class="workflow-steps">
          <div class="workflow-step">
            <div class="step-number">01</div>
            <div class="step-content">
              <h3>上传论文</h3>
              <p>支持 PDF 文件上传或直接粘贴 arXiv 链接，一键开始处理。</p>
            </div>
          </div>

          <div class="workflow-connector"></div>

          <div class="workflow-step">
            <div class="step-number">02</div>
            <div class="step-content">
              <h3>智能处理</h3>
              <p>六阶段流水线自动解析、结构化、规划、转写，实时查看进度。</p>
            </div>
          </div>

          <div class="workflow-connector"></div>

          <div class="workflow-step">
            <div class="step-number">03</div>
            <div class="step-content">
              <h3>开始阅读</h3>
              <p>在沉浸式阅读器中浏览易读版论文，支持下载离线阅读。</p>
            </div>
          </div>
        </div>
      </section>

      <!-- CTA Section -->
      <section class="cta-section">
        <div class="cta-card">
          <div class="cta-content">
            <h2>{{ auth.isAuthenticated ? '继续你的论文阅读之旅' : '准备好提升你的阅读效率了吗？' }}</h2>
            <p>{{ auth.isAuthenticated ? '进入工作台，查看处理进度与讲义' : '立即注册，免费体验论文易读化转写服务' }}</p>
          </div>
          <div class="cta-actions">
            <router-link :to="auth.isAuthenticated ? '/home' : '/register'" class="btn btn-primary btn-lg">
              {{ auth.isAuthenticated ? '进入应用' : '立即开始' }}
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M6 3l5 5-5 5" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </router-link>
          </div>
        </div>
      </section>
    </main>

    <!-- Footer -->
    <footer class="landing-footer">
      <div class="footer-inner">
        <div class="footer-brand">
          <BrandLogo />
          <span class="footer-brand-text">纸慧 PaperWise</span>
        </div>
        <p class="footer-desc">让每一篇学术论文都变得通俗易懂</p>
        <div class="footer-bottom">
          <span class="copyright">© 2026 纸慧 PaperWise. All rights reserved.</span>
        </div>
      </div>
    </footer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import client, { onNetworkUnavailable } from '../api/client'
import { useAuthStore } from '../stores/auth'
import BrandLogo from '../components/BrandLogo.vue'

const router = useRouter()
const auth = useAuthStore()

async function handleLogout() {
  await auth.logout()
  router.push('/')
}

const HEALTH_POLL_MS = 12000

const backendUp = ref(false)
const checked = ref(false)
let offNetwork = null

async function checkHealth() {
  try {
    const resp = await client.get('/api/health')
    backendUp.value = resp.status === 200 && resp.data?.status === 'ok'
  } catch {
    backendUp.value = false
  } finally {
    checked.value = true
  }
}

let pollTimer = null

onMounted(() => {
  checkHealth()
  pollTimer = window.setInterval(checkHealth, HEALTH_POLL_MS)
  offNetwork = onNetworkUnavailable(() => {
    backendUp.value = false
    checked.value = true
  })
})

onUnmounted(() => {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
  if (offNetwork) offNetwork()
})

const statusClass = computed(() => (backendUp.value ? 'ok' : 'down'))
const statusText = computed(() => {
  if (!checked.value) return '检测中…'
  return backendUp.value ? '服务正常' : '服务离线'
})
</script>

<style scoped>
.landing-page {
  min-height: 100vh;
  background: var(--bg-primary);
  color: var(--text-primary);
  display: flex;
  flex-direction: column;
}

/* Navigation */
.landing-nav {
  position: sticky;
  top: 0;
  z-index: var(--z-sticky);
  background: rgba(250, 250, 250, 0.8);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border-primary);
}

:root[data-theme="dark"] .landing-nav {
  background: rgba(24, 24, 27, 0.8);
}

.nav-inner {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 24px;
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.nav-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  cursor: pointer;
  user-select: none;
}

.nav-logo {
  width: 28px;
  height: 28px;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  justify-content: center;
}

.nav-logo svg {
  width: 28px;
  height: 28px;
}

.nav-brand-text {
  font-size: var(--font-size-md);
  letter-spacing: 0.2px;
}

.nav-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: var(--radius-full);
  font-size: var(--font-size-xs);
  font-weight: 500;
  margin-right: 6px;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.status-pill.ok {
  background: var(--success-bg);
  color: var(--success-text);
}
.status-pill.ok .status-dot {
  background: var(--success-text);
}

.status-pill.down {
  background: var(--error-bg);
  color: var(--error-text);
}
.status-pill.down .status-dot {
  background: var(--error-text);
}

/* 按钮 */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 16px;
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
  width: 14px;
  height: 14px;
}

.btn-sm {
  padding: 6px 14px;
  font-size: var(--font-size-xs);
}

.btn-lg {
  padding: 12px 24px;
  font-size: var(--font-size-base);
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
  box-shadow: var(--shadow-sm);
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

/* Hero */
.hero-section {
  padding: 80px 24px 60px;
}

.hero-inner {
  max-width: 1200px;
  margin: 0 auto;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 60px;
  align-items: center;
}

.hero-content {
  animation: fadeInUp 0.6s ease-out;
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.hero-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-full);
  font-size: var(--font-size-xs);
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 24px;
}

.badge-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--success-text);
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.hero-title {
  margin: 0 0 20px;
  font-size: var(--font-size-5xl);
  font-weight: 700;
  line-height: 1.2;
  letter-spacing: -1px;
  color: var(--text-primary);
}

.hero-title .highlight {
  background: linear-gradient(135deg, var(--text-primary) 0%, var(--text-tertiary) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.hero-desc {
  margin: 0 0 32px;
  font-size: var(--font-size-md);
  line-height: 1.7;
  color: var(--text-secondary);
  max-width: 480px;
}

.hero-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 48px;
  flex-wrap: wrap;
}

.hero-stats {
  display: flex;
  align-items: center;
  gap: 24px;
}

.stat-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-num {
  font-size: var(--font-size-xl);
  font-weight: 700;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.stat-label {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
}

.stat-divider {
  width: 1px;
  height: 36px;
  background: var(--border-primary);
}

/* Hero Visual */
.hero-visual {
  display: flex;
  justify-content: center;
  align-items: center;
  animation: fadeInUp 0.6s ease-out 0.2s both;
}

.visual-card {
  width: 100%;
  max-width: 420px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  transform: perspective(1000px) rotateY(-5deg) rotateX(2deg);
  transition: transform 0.4s ease;
}

.visual-card:hover {
  transform: perspective(1000px) rotateY(0) rotateX(0);
}

.visual-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border-primary);
  background: var(--bg-secondary);
}

.visual-dots {
  display: flex;
  gap: 5px;
}

.visual-dots span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--bg-tertiary);
}

.visual-dots span:first-child { background: var(--error-text); opacity: 0.6; }
.visual-dots span:nth-child(2) { background: var(--warning-text); opacity: 0.6; }
.visual-dots span:nth-child(3) { background: var(--success-text); opacity: 0.6; }

.visual-title {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  margin-left: 8px;
}

.visual-body {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.visual-line {
  height: 10px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
}

.visual-line.long { width: 100%; }
.visual-line.medium { width: 75%; }
.visual-line.short { width: 50%; }

.visual-block {
  display: flex;
  gap: 12px;
  padding: 14px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  border-left: 3px solid var(--success-text);
  margin: 4px 0;
}

.block-icon {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--success-bg);
  color: var(--success-text);
  border-radius: var(--radius-sm);
}

.block-icon svg {
  width: 16px;
  height: 16px;
}

.block-content {
  flex: 1;
  min-width: 0;
}

.block-title {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 2px;
}

.block-desc {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
}

/* Section Header */
.section-header {
  text-align: center;
  margin-bottom: 48px;
}

.section-tag {
  display: inline-block;
  padding: 4px 12px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-full);
  font-size: var(--font-size-xs);
  font-weight: 500;
  color: var(--text-tertiary);
  margin-bottom: 16px;
}

.section-title {
  margin: 0 0 12px;
  font-size: var(--font-size-3xl);
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.5px;
}

.section-desc {
  margin: 0;
  font-size: var(--font-size-base);
  color: var(--text-tertiary);
  max-width: 520px;
  margin-left: auto;
  margin-right: auto;
  line-height: 1.6;
}

/* Features */
.feature-section {
  padding: 80px 24px;
  background: var(--bg-secondary);
}

.feature-grid {
  max-width: 1200px;
  margin: 0 auto;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
}

.feature-card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-lg);
  padding: 28px;
  transition: all var(--transition-normal);
}

.feature-card:hover {
  border-color: var(--border-strong);
  transform: translateY(-4px);
  box-shadow: var(--shadow-md);
}

.feature-icon {
  width: 44px;
  height: 44px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 18px;
}

.feature-icon svg {
  width: 22px;
  height: 22px;
}

.icon-purple { background: #f3e8ff; color: #7c3aed; }
.icon-green { background: var(--success-bg); color: var(--success-text); }
.icon-orange { background: #fff7ed; color: #c2410c; }
.icon-blue { background: var(--info-bg); color: var(--info-text); }
.icon-pink { background: #fdf2f8; color: #be185d; }
.icon-teal { background: #ecfeff; color: #0e7490; }

:root[data-theme="dark"] .icon-purple { background: #3b0764; }
:root[data-theme="dark"] .icon-orange { background: #431407; }
:root[data-theme="dark"] .icon-pink { background: #500724; }
:root[data-theme="dark"] .icon-teal { background: #083344; }

.feature-title {
  margin: 0 0 10px;
  font-size: var(--font-size-md);
  font-weight: 600;
  color: var(--text-primary);
}

.feature-desc {
  margin: 0;
  font-size: var(--font-size-sm);
  line-height: 1.7;
  color: var(--text-secondary);
}

/* Workflow */
.workflow-section {
  padding: 80px 24px;
}

.workflow-steps {
  max-width: 900px;
  margin: 0 auto;
  display: flex;
  align-items: flex-start;
  gap: 16px;
}

.workflow-step {
  flex: 1;
  text-align: center;
}

.step-number {
  width: 56px;
  height: 56px;
  margin: 0 auto 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--font-size-xl);
  font-weight: 700;
  color: var(--text-inverse);
  background: var(--text-primary);
  border-radius: var(--radius-lg);
}

.step-content h3 {
  margin: 0 0 8px;
  font-size: var(--font-size-md);
  font-weight: 600;
  color: var(--text-primary);
}

.step-content p {
  margin: 0;
  font-size: var(--font-size-sm);
  line-height: 1.6;
  color: var(--text-tertiary);
}

.workflow-connector {
  width: 48px;
  height: 2px;
  background: var(--border-primary);
  margin-top: 28px;
  flex-shrink: 0;
}

/* CTA */
.cta-section {
  padding: 80px 24px;
}

.cta-card {
  max-width: 1000px;
  margin: 0 auto;
  background: var(--text-primary);
  color: var(--text-inverse);
  border-radius: var(--radius-2xl);
  padding: 56px 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 32px;
}

.cta-content h2 {
  margin: 0 0 8px;
  font-size: var(--font-size-2xl);
  font-weight: 700;
}

.cta-content p {
  margin: 0;
  font-size: var(--font-size-base);
  opacity: 0.8;
}

.cta-actions .btn-primary {
  background: var(--text-inverse);
  color: var(--text-primary);
  border-color: var(--text-inverse);
}

.cta-actions .btn-primary:hover {
  background: var(--bg-tertiary);
  border-color: var(--bg-tertiary);
}

/* Footer */
.landing-footer {
  border-top: 1px solid var(--border-primary);
  padding: 48px 24px 32px;
  margin-top: auto;
}

.footer-inner {
  max-width: 1200px;
  margin: 0 auto;
  text-align: center;
}

.footer-brand {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-bottom: 12px;
}

.footer-logo {
  width: 28px;
  height: 28px;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  justify-content: center;
}

.footer-logo svg {
  width: 28px;
  height: 28px;
}

.footer-brand-text {
  font-size: var(--font-size-md);
  font-weight: 700;
  color: var(--text-primary);
}

.footer-desc {
  margin: 0 0 24px;
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
}

.footer-bottom {
  padding-top: 20px;
  border-top: 1px solid var(--border-secondary);
}

.copyright {
  font-size: var(--font-size-xs);
  color: var(--text-quaternary);
}

/* 响应式 */
@media (max-width: 768px) {
  .navbar {
    padding: 0 16px;
  }

  .nav-links {
    display: none;
  }

  .hero {
    flex-direction: column;
    text-align: center;
    padding: 40px 20px;
    gap: 40px;
  }

  .hero-content {
    max-width: 100%;
  }

  .hero-title {
    font-size: 32px;
  }

  .hero-desc {
    font-size: var(--font-size-base);
  }

  .hero-actions {
    justify-content: center;
  }

  .hero-preview {
    width: 100%;
    max-width: 400px;
  }

  .section {
    padding: 48px 20px;
  }

  .section-title {
    font-size: var(--font-size-xl);
  }

  .features-grid {
    grid-template-columns: 1fr;
    gap: 12px;
  }

  .feature-card {
    padding: 20px;
  }

  .steps {
    flex-direction: column;
    gap: 20px;
  }

  .step-item {
    text-align: center;
  }

  .step-arrow {
    display: none;
  }

  .cta-card {
    padding: 32px 20px;
  }

  .cta-title {
    font-size: var(--font-size-xl);
  }

  .cta-actions {
    flex-direction: column;
  }

  .cta-actions .btn {
    width: 100%;
  }

  .footer-content {
    grid-template-columns: 1fr;
    gap: 24px;
  }

  .footer-bottom {
    text-align: center;
  }
}
</style>
