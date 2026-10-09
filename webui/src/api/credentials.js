// 第三方密钥管理 API（US1）：统一走既有 axios 单例，不新建请求实例
import client from './client'

export function listCredentials() {
  return client.get('/api/credentials')
}

export function saveCredential(service, secret) {
  return client.put(`/api/credentials/${service}`, { secret })
}

export function deleteCredential(service) {
  return client.delete(`/api/credentials/${service}`)
}
