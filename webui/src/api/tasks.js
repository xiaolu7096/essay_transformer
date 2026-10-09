// 任务 API（US2/US3）：统一走既有 axios 单例
import client from './client'

export function createFromUrl(source) {
  return client.post('/api/tasks/from-url', { source })
}

export function createUpload(file) {
  // multipart：不手动设 Content-Type，交给浏览器带 boundary
  const form = new FormData()
  form.append('file', file)
  return client.post('/api/tasks/upload', form)
}

export function listTasks() {
  return client.get('/api/tasks')
}

export function getTask(id) {
  return client.get(`/api/tasks/${id}`)
}

export function cancelTask(id) {
  return client.post(`/api/tasks/${id}/cancel`)
}

export function deleteTask(id) {
  return client.delete(`/api/tasks/${id}`)
}
