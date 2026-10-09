<template>
  <AppLayout :username="auth.username" @logout="onLogout">
    <section class="home">
      <h2>欢迎，{{ auth.username || '…' }}</h2>
      <p class="hint">这里是受保护的内部页面占位（FR-007）。论文转写任务将在后续版本开放。</p>

      <h3>渲染基线示例（FR-016）</h3>
      <div class="demo-card">
        <MarkdownView :content="demoMarkdown" />
      </div>
    </section>
  </AppLayout>
</template>

<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AppLayout from '../layouts/AppLayout.vue'
import MarkdownView from '../components/MarkdownView.vue'
import { useAuthStore } from '../stores/auth'

const demoMarkdown = `## 二次方程求根

对于形如 $ax^2 + bx + c = 0$（$a \\ne 0$）的方程，其解为：

$$
x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}
$$

**判别式** $\\Delta = b^2 - 4ac$ 决定根的性质：

- $\\Delta > 0$：两个不相等的实根
- $\\Delta = 0$：一个重根
- $\\Delta < 0$：两个共轭复根
`

const auth = useAuthStore()
const router = useRouter()

onMounted(() => {
  // 刷新直达 /home 时，store 可能尚未拉取本人信息
  if (!auth.user && auth.isAuthenticated) {
    auth.fetchMe().catch(() => {})
  }
})

async function onLogout() {
  await auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.home {
  max-width: 720px;
  margin: 24px auto;
}
.hint {
  color: #6b7280;
}
.demo-card {
  margin-top: 12px;
  padding: 16px 20px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
}
</style>
