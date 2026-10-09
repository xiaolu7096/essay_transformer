<template>
  <div class="brand-logo" :class="{ 'brand-logo--large': size === 'large' }">
    <svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <!-- 绿色圆角方块 -->
      <rect x="1" y="1" width="38" height="38" rx="10" fill="var(--logo-bg, #2f7d54)" />
      <!-- 书页嫩芽 -->
      <path
        d="M11.5 27.5c0-8 5.5-13.5 13-15.5 1.8 7.5-1.8 14.5-8.4 16.3-2.7 0-4.6-.8-4.6-.8z"
        fill="var(--logo-leaf, #ffffff)"
        opacity="0.96"
      />
      <!-- 叶脉 -->
      <path
        d="M14.5 22c1.6-3.2 4.2-5.2 7.4-5.8"
        stroke="var(--logo-bg, #2f7d54)"
        stroke-width="1.3"
        stroke-linecap="round"
        fill="none"
        opacity="0.55"
      />
    </svg>
  </div>
</template>

<script setup>
defineProps({
  size: {
    type: String,
    default: 'normal' // normal | large
  }
})
</script>

<style scoped>
.brand-logo {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.brand-logo--large {
  width: 56px;
  height: 56px;
}

.brand-logo svg {
  width: 100%;
  height: 100%;
}
</style>
