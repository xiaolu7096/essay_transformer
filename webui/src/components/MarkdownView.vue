<template>
  <!-- FR-016 渲染基建：Markdown + KaTeX 公式；内容为受信的本地/自有产出，不开放原始 HTML -->
  <div class="markdown-body" v-html="rendered" data-testid="markdown-view" />
</template>

<script setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import katex from 'katex'
import 'katex/dist/katex.min.css'

const props = defineProps({
  content: {
    type: String,
    default: '',
  },
})

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: false,
})

// 先抽取公式渲染成 HTML，以纯文本占位符替换，再走 Markdown 解析，最后回填。
// display: $$...$$（可跨行）；inline: $...$（单行）。
const DISPLAY_RE = /\$\$([\s\S]+?)\$\$/g
const INLINE_RE = /\$([^$\n]+?)\$/g

function renderMath(tex, displayMode) {
  return katex.renderToString(tex, {
    displayMode,
    throwOnError: false,
    output: 'html',
  })
}

function renderMarkdown(src) {
  const parts = []
  const take = (html) => {
    const key = `@@ETMATH${parts.length}@@`
    parts.push(html)
    return key
  }

  let text = src.replace(DISPLAY_RE, (_m, tex) => take(renderMath(tex.trim(), true)))
  text = text.replace(INLINE_RE, (_m, tex) => take(renderMath(tex.trim(), false)))

  let html = md.render(text)
  html = html.replace(/@@ETMATH(\d+)@@/g, (_m, i) => parts[Number(i)])
  return html
}

const rendered = computed(() => renderMarkdown(props.content || ''))
</script>

<style scoped>
.markdown-body {
  line-height: 1.75;
  color: #1f2937;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  margin: 1.2em 0 0.6em;
  line-height: 1.3;
}
.markdown-body :deep(p) {
  margin: 0.6em 0;
}
.markdown-body :deep(code) {
  background: #f3f4f6;
  padding: 0.15em 0.4em;
  border-radius: 4px;
  font-size: 0.9em;
}
.markdown-body :deep(pre) {
  background: #f3f4f6;
  padding: 12px 14px;
  border-radius: 8px;
  overflow-x: auto;
}
.markdown-body :deep(pre code) {
  background: transparent;
  padding: 0;
}
.markdown-body :deep(blockquote) {
  margin: 0.8em 0;
  padding: 0.2em 1em;
  border-left: 3px solid #d1d5db;
  color: #6b7280;
}
.markdown-body :deep(table) {
  border-collapse: collapse;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #e5e7eb;
  padding: 6px 12px;
}
</style>
