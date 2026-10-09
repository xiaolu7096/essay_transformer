<template>
  <n-config-provider
    :locale="zhCN"
    :date-locale="dateZhCN"
    :theme="naiveTheme"
    :theme-overrides="themeOverrides"
  >
    <n-message-provider>
      <n-dialog-provider>
        <router-view v-slot="{ Component, route }">
          <transition name="route-fade" mode="out-in">
            <component :is="Component" :key="route.fullPath" />
          </transition>
        </router-view>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup>
// 统一主题/中文 locale 与全局反馈（message/dialog）基座
// theme: 通过 useTheme 控制全局 data-theme，Naive UI 同步切换
import { computed } from 'vue'
import { NConfigProvider, NDialogProvider, NMessageProvider, darkTheme } from 'naive-ui'
import { dateZhCN, zhCN } from 'naive-ui'
import { useTheme } from './composables/useTheme'

const { theme } = useTheme()

// Naive UI 暗色主题（内置）
const naiveTheme = computed(() => (theme.value === 'dark' ? darkTheme : null))

// Naive UI 主题覆盖 —— 与柔墨设计 token 对齐
const themeOverrides = computed(() => {
  if (theme.value === 'dark') {
    return {
      common: {
        primaryColor: '#a1a1aa',
        primaryColorHover: '#d4d4d8',
        primaryColorPressed: '#71717a',
        primaryColorSuppl: '#a1a1aa',
        bodyColor: '#18181b',
        textColorBase: '#e4e4e7',
        textColor1: '#e4e4e7',
        textColor2: '#a1a1aa',
        textColor3: '#71717a',
        borderColor: '#3f3f46',
        borderRadius: '10px',
        borderRadiusSmall: '6px',
      },
      Button: {
        borderRadiusMedium: '10px',
      },
      Input: {
        borderRadiusMedium: '10px',
      },
      Card: {
        borderRadius: '14px',
      },
    }
  }
  return {
    common: {
      primaryColor: '#27272a',
      primaryColorHover: '#3f3f46',
      primaryColorPressed: '#52525b',
      primaryColorSuppl: '#27272a',
      bodyColor: '#fafafa',
      textColorBase: '#27272a',
      textColor1: '#27272a',
      textColor2: '#52525b',
      textColor3: '#71717a',
      borderColor: '#e4e4e7',
      borderRadius: '10px',
      borderRadiusSmall: '6px',
    },
    Button: {
      borderRadiusMedium: '10px',
    },
    Input: {
      borderRadiusMedium: '10px',
    },
    Card: {
      borderRadius: '14px',
    }
  }
})
</script>

<style>
/* 路由过渡动画 */
.route-fade-enter-active,
.route-fade-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.route-fade-enter-from {
  opacity: 0;
  transform: translateY(6px);
}

.route-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@media (prefers-reduced-motion: reduce) {
  .route-fade-enter-active,
  .route-fade-leave-active {
    transition: none;
  }
}
</style>
