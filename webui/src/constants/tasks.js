// 任务展示层常量（US3）：状态五色徽标、六阶段中文名、失败分类配色
export const STATUS_META = {
  queued: { label: '排队中', type: 'default' },
  running: { label: '处理中', type: 'info' },
  succeeded: { label: '已完成', type: 'success' },
  failed: { label: '失败', type: 'error' },
  cancelled: { label: '已取消', type: 'warning' },
}

export const STAGES = [
  { key: 'ingest', label: '摄取' },
  { key: 'parse', label: '解析' },
  { key: 'normalize', label: '结构化' },
  { key: 'plan', label: '规划' },
  { key: 'rewrite', label: '转写' },
  { key: 'assemble', label: '成稿' },
]

export const STAGE_LABEL = Object.fromEntries(STAGES.map((s) => [s.key, s.label]))

export const FAILURE_CATEGORY_META = {
  input: { label: '输入问题', type: 'warning' },
  service: { label: '外部服务问题', type: 'error' },
  system: { label: '系统问题', type: 'error' },
}
