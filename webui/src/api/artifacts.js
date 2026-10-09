/**
 * Spec 003 US2：成品阅读器 API。
 * 图片接口需要 Bearer 鉴权，不能直接放进 <img src>，因此 fetch 取 blob
 * 后换 objectURL，并按任务缓存；离开阅读器时统一 revoke。
 */
import { getToken } from './token'
import { dispatchUnauthorized } from './client'
import { isSafeArtName } from '../lib/markdown'

const API_BASE = '/api/tasks'

// key: `${taskId}\u0000${name}` -> objectURL
const urlCache = new Map()

function authHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/** 归一化后端错误：保留 status 与扁平 code，供视图区分固定中文态。 */
function toError(err) {
  const status = err.response?.status
  const code = err.response?.data?.code || null
  const e = new Error(err.response?.data?.detail || '请求失败')
  e.status = status
  e.code = code
  return e
}

export async function getReader(taskId) {
  let resp
  try {
    resp = await fetch(`${API_BASE}/${encodeURIComponent(taskId)}/reader`, {
      headers: { Accept: 'application/json', ...authHeaders() },
    })
  } catch {
    const e = new Error('网络不可用，请稍后重试')
    e.status = 0
    e.code = 'network_error'
    throw e
  }
  if (resp.status === 401) {
    dispatchUnauthorized(`${API_BASE}/${taskId}/reader`)
    const e = new Error('登录已过期，请重新登录')
    e.status = 401
    throw e
  }
  if (!resp.ok) {
    let body = null
    try {
      body = await resp.json()
    } catch {
      body = null
    }
    const e = new Error(body?.detail || '成品加载失败')
    e.status = resp.status
    e.code = body?.code || null
    throw e
  }
  return resp.json()
}

/**
 * 取成品图片 objectURL（同任务同名复用缓存）。
 * 非法名直接 reject；调用方负责失败占位。
 */
export async function fetchImageBlob(taskId, name) {
  if (!isSafeArtName(name)) {
    const e = new Error('非法图片名')
    e.status = 404
    e.code = 'invalid_name'
    throw e
  }
  const key = `${taskId}\u0000${name}`
  const cached = urlCache.get(key)
  if (cached) {
    return cached
  }

  let resp
  try {
    const url = `${API_BASE}/${encodeURIComponent(taskId)}/artifacts/images/${encodeURIComponent(name)}`
    resp = await fetch(url, { headers: authHeaders() })
  } catch {
    const e = new Error('网络不可用')
    e.status = 0
    throw e
  }
  if (resp.status === 401) {
    dispatchUnauthorized(`${API_BASE}/${taskId}/artifacts/images/${name}`)
    const e = new Error('登录已过期，请重新登录')
    e.status = 401
    throw e
  }
  if (!resp.ok) {
    const e = new Error('图片不存在')
    e.status = resp.status
    throw e
  }

  const blob = await resp.blob()
  const objectUrl = URL.createObjectURL(blob)
  urlCache.set(key, objectUrl)
  return objectUrl
}

/** 释放某任务名下全部 objectURL（阅读器卸载时调用）。 */
export function revokeTaskImages(taskId) {
  const prefix = `${taskId}\u0000`
  for (const [key, url] of urlCache.entries()) {
    if (key.startsWith(prefix)) {
      URL.revokeObjectURL(url)
      urlCache.delete(key)
    }
  }
}

function filenameFromDisposition(disposition, fallback) {
  if (!disposition) return fallback
  // 优先 RFC 5987 filename*=UTF-8''%E4...
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  if (star && star[1]) {
    try {
      return decodeURIComponent(star[1].trim())
    } catch {
      // 落到 ASCII filename
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition)
  return plain && plain[1] ? plain[1].trim() : fallback
}

/**
 * Spec 003 US3：Bearer 取 zip blob 并触发浏览器保存。
 * 不打开裸 URL（鉴权头无法通过导航携带）。
 */
export async function downloadArtifactZip(taskId) {
  let resp
  try {
    resp = await fetch(
      `${API_BASE}/${encodeURIComponent(taskId)}/artifact/download`,
      { headers: authHeaders() },
    )
  } catch {
    const e = new Error('网络不可用，请稍后重试')
    e.status = 0
    throw e
  }
  if (resp.status === 401) {
    dispatchUnauthorized(`${API_BASE}/${taskId}/artifact/download`)
    const e = new Error('登录已过期，请重新登录')
    e.status = 401
    throw e
  }
  if (!resp.ok) {
    let body = null
    try {
      body = await resp.json()
    } catch {
      body = null
    }
    const e = new Error(body?.detail || '下载失败')
    e.status = resp.status
    e.code = body?.code || null
    throw e
  }

  const blob = await resp.blob()
  const fallback = `paper-${taskId.slice(0, 8)}.zip`
  const filename = filenameFromDisposition(
    resp.headers.get('Content-Disposition') || '',
    fallback,
  )
  const url = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    // 等浏览器接管下载后再释放
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return filename
}
