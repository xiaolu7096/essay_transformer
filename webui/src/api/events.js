// Spec 003 US1（T009）：任务实时事件 SSE 客户端。
//
// 硬约束：
// - 用 fetch + ReadableStream 解析 SSE；令牌只放在 Authorization 头，
//   绝不进 URL（不用原生 EventSource，它无法自定义请求头）。
// - 事件是瞬时消息：重连后以服务端首帧 snapshot 收敛，不做本地回放。
// - 网络错误按 1→2→4→8→10 秒指数退避自动重连；连续 ≥5 次失败
//   （约 30 秒）回调 onShouldFallback，由 store 降级为 3 秒快照轮询。
// - 401 交全局处理且不重连；404 回调 onError 且不重连；
//   terminal 帧后服务端关流，本地同步停止；AbortSignal 中止即停。
import { getToken } from './token'
import { dispatchUnauthorized } from './client'

const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 10000]
const FALLBACK_AFTER_FAILURES = 5
// 服务端每 15s 发一帧 PING 心跳（后端 PING_INTERVAL_SECONDS=15）。
// 半开连接（服务端宕机但代理不立刻断连）时 reader.read() 会永久挂起，
// 既不报错也不结束，故客户端必须自己按字节空闲判定活性：
// 超过 20s（PING 间隔 1.33 倍，留代理抖动余量）未收到任何字节即视为死链，
// abort 当前连接并走退避重连，与普通网络错误落入同一重连/降级链路。
const IDLE_TIMEOUT_MS = 20000

/**
 * 打开一条任务事件流。
 * @param {string} taskId
 * @param {object} handlers onSnapshot/onUpdate/onTerminal/
 *   onError/onShouldFallback/onConnected，外加 signal（AbortSignal）
 * @returns {{stop: () => void}} 主动关闭句柄
 */
export function openTaskStream(taskId, handlers) {
  const {
    onSnapshot,
    onUpdate,
    onTerminal,
    onError,
    onShouldFallback,
    onConnected,
    signal,
  } = handlers

  const url = `/api/tasks/${encodeURIComponent(taskId)}/events`
  let stopped = false
  let retryTimer = null
  let inFlight = null
  let idleTimer = null
  let failures = 0
  let fallbackNotified = false

  function stop() {
    if (stopped) return
    stopped = true
    if (retryTimer) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    if (idleTimer) {
      clearTimeout(idleTimer)
      idleTimer = null
    }
    inFlight?.abort()
    inFlight = null
  }

  signal?.addEventListener('abort', stop, { once: true })
  if (signal?.aborted) {
    stop()
    return { stop }
  }

  // 网络层失败/非 200（非 401/404）：退避重连；≥5 次通知降级
  function scheduleRetry() {
    if (stopped || signal?.aborted) return
    failures += 1
    if (failures >= FALLBACK_AFTER_FAILURES && !fallbackNotified) {
      fallbackNotified = true
      onShouldFallback?.(new Error('事件通道连续不可用，降级为轮询'))
      stop()
      return
    }
    const delay =
      RETRY_DELAYS_MS[Math.min(failures - 1, RETRY_DELAYS_MS.length - 1)]
    retryTimer = setTimeout(() => {
      retryTimer = null
      void connect()
    }, delay)
  }

  async function connect() {
    if (stopped || signal?.aborted) return
    const controller = new AbortController()
    inFlight = controller
    signal?.addEventListener('abort', () => controller.abort(), { once: true })

    let response
    try {
      response = await fetch(url, {
        method: 'GET',
        headers: {
          Accept: 'text/event-stream',
          Authorization: `Bearer ${getToken()}`,
        },
        signal: controller.signal,
      })
    } catch {
      if (stopped || signal?.aborted) return
      scheduleRetry()
      return
    } finally {
      if (inFlight === controller) inFlight = null
    }

    // 401：清空会话/跳登录（与 axios 拦截器同一出口），不重连
    if (response.status === 401) {
      dispatchUnauthorized(url)
      stop()
      return
    }
    // 404：任务不存在或越权，不重连（与 REST 404 口径一致）
    if (response.status === 404) {
      onError?.(new Error('任务不存在'))
      stop()
      return
    }
    if (!response.ok || !response.body) {
      scheduleRetry()
      return
    }

    // 建流成功：后端保证首帧即 snapshot，首帧到达视为通道真正可用
    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    let currentEvent = 'message'
    let dataLines = []
    let firstFrame = true

    // 空闲看门狗：每次读到任意字节（含 PING 心跳注释帧）都重新计时；
    // 超时则 abort 此连接，reader.read() 会抛错并落入下方 catch 退避重连。
    const armIdleWatchdog = () => {
      if (idleTimer) clearTimeout(idleTimer)
      idleTimer = setTimeout(() => {
        idleTimer = null
        controller.abort(new Error('事件流空闲超时'))
      }, IDLE_TIMEOUT_MS)
    }
    const clearIdleWatchdog = () => {
      if (idleTimer) {
        clearTimeout(idleTimer)
        idleTimer = null
      }
    }

    function dispatchFrame() {
      if (dataLines.length === 0) {
        currentEvent = 'message'
        return
      }
      const data = dataLines.join('\n')
      dataLines = []
      const event = currentEvent
      currentEvent = 'message'

      let payload
      try {
        payload = JSON.parse(data)
      } catch {
        return
      }
      if (firstFrame) {
        firstFrame = false
        failures = 0 // 收到首帧：此前连续失败计数归零
        onConnected?.(payload)
      }
      if (event === 'snapshot') onSnapshot?.(payload)
      else if (event === 'update') onUpdate?.(payload)
      else if (event === 'terminal') {
        onTerminal?.(payload)
        stop()
      }
    }

    try {
      for (;;) {
        armIdleWatchdog()
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let nl
        while ((nl = buffer.indexOf('\n')) >= 0) {
          let line = buffer.slice(0, nl)
          buffer = buffer.slice(nl + 1)
          if (line.endsWith('\r')) line = line.slice(0, -1)
          if (line === '') {
            dispatchFrame()
            continue
          }
          if (line.startsWith(':')) continue // 心跳/注释帧：忽略，不改状态
          if (line.startsWith('event:')) {
            currentEvent = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            dataLines.push(
              line.startsWith('data: ') ? line.slice(6) : line.slice(5),
            )
          }
        }
      }
    } catch {
      if (stopped || signal?.aborted) return
      scheduleRetry()
      return
    } finally {
      clearIdleWatchdog()
    }

    // 服务端关流但未见 terminal（代理截断等）：退避重连，以 snapshot 收敛
    if (stopped || signal?.aborted) return
    scheduleRetry()
  }

  void connect()
  return { stop }
}
