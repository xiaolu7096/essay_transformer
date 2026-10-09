// 任务状态（Pinia）：列表/详情。
// Spec 003 US1：以 SSE 事件订阅替换 002 的 3 秒 setInterval——
// 列表对每个 queued/running 任务各开一条流，详情订阅单任务；
// snapshot 整体收敛，update 仅接受 seq 不倒退的帧，terminal 后关流。
// 通道连续不可用（≥5 次/约 30 秒）才降级为 3 秒快照轮询，
// 下一次成功建流（收到首帧）立即恢复实时。同一任务同一时刻
// 只允许一种持续刷新（流或轮询），绝不并存。
import { defineStore } from 'pinia'
import * as tasksApi from '../api/tasks'
import { openTaskStream } from '../api/events'

const POLL_INTERVAL_MS = 3000
const ACTIVE_STATUSES = new Set(['queued', 'running'])
// 事件帧字段（载荷不含 source_kind/source_ref/created_at，需合并保留）
const EVENT_FIELDS = [
  'id',
  'status',
  'stage',
  'progress',
  'message',
  'fake_mode',
  'failure',
  'result',
  'started_at',
  'finished_at',
  'seq',
]

export const useTasksStore = defineStore('tasks', {
  state: () => ({
    tasks: [],
    currentTask: null,
    loading: false,
    detailLoading: false,
    error: '',
    // true = 事件通道不可用，当前以 3 秒快照轮询兜底
    realtimeDegraded: false,
  }),

  getters: {
    hasActive: (state) =>
      state.tasks.some((t) => ACTIVE_STATUSES.has(t.status)) ||
      (state.currentTask && ACTIVE_STATUSES.has(state.currentTask.status)),
  },

  actions: {
    async fetchList() {
      this.loading = true
      this.error = ''
      try {
        const { data } = await tasksApi.listTasks()
        this.tasks = data.tasks
      } catch (e) {
        if (e.response?.status !== 401) this.error = '任务列表加载失败'
      } finally {
        this.loading = false
      }
    },

    async fetchDetail(id) {
      this.detailLoading = true
      this.error = ''
      try {
        const { data } = await tasksApi.getTask(id)
        this.currentTask = data
        // 同步刷新列表中对应行
        const idx = this.tasks.findIndex((t) => t.id === id)
        if (idx >= 0) this.tasks[idx] = data
        return data
      } catch (e) {
        if (e.response?.status !== 401) this.error = '任务详情加载失败'
        throw e
      } finally {
        this.detailLoading = false
      }
    },

    async removeTask(id) {
      await tasksApi.deleteTask(id)
      // 先关闭可能存在的订阅句柄，再从本地状态移除
      if (this._rt) {
        this._closeHandle(id)
        this._rt.scopes.forEach((ids) => ids.delete(id))
        this._rt.seq.delete(id)
      }
      this.tasks = this.tasks.filter((t) => t.id !== id)
      if (this.currentTask?.id === id) this.currentTask = null
    },

    // ------------------------------------------------------------------
    // 实时订阅（私有结构不进 state，避免被 reactive 包装）
    // ------------------------------------------------------------------

    _rtInit() {
      if (this._rt) return
      this._rt = {
        handles: new Map(), // taskId -> {stop}
        seq: new Map(), // taskId -> 最近应用的 seq
        scopes: new Map(), // scope -> Set<taskId>（'list'/'detail'）
        pollTimer: null,
        degraded: false,
      }
    },

    _isActive(id) {
      const row = this.tasks.find((t) => t.id === id)
      if (row) return ACTIVE_STATUSES.has(row.status)
      return (
        this.currentTask?.id === id &&
        ACTIVE_STATUSES.has(this.currentTask.status)
      )
    },

    _wantedByScopes(id) {
      for (const ids of this._rt.scopes.values()) {
        if (ids.has(id)) return true
      }
      return false
    },

    _acquire(id) {
      const rt = this._rt
      // 已有流（含其内部退避重连中）不重复开；降级期间由轮询 tick
      // 周期性调用本方法尝试恢复，首帧到达即 _onStreamRecovered。
      if (rt.handles.has(id)) return
      const handle = openTaskStream(id, {
        onSnapshot: (p) => this._applyEvent(id, p, true),
        onUpdate: (p) => this._applyEvent(id, p, false),
        onTerminal: (p) => {
          this._applyEvent(id, p, true)
          this._closeHandle(id)
        },
        onError: () => {
          // 404 等：流不可用；若任务仍在视图内，下一轮 reconcile 会按
          // 快照刷新收敛（REST GET 404 由调用方自行展示不存在）。
          this._closeHandle(id)
        },
        onConnected: () => this._onStreamRecovered(),
        onShouldFallback: () => {
          this._enterFallback()
          // 已在降级中（重复通知）时 _enterFallback 早退，需自行清句柄
          this._closeHandle(id)
        },
      })
      rt.handles.set(id, handle)
    },

    _closeHandle(id) {
      const handle = this._rt.handles.get(id)
      if (handle) {
        handle.stop()
        this._rt.handles.delete(id)
      }
    },

    /**
     * 按当前作用域与任务状态收敛订阅集合：
     * 需要且活动但无流 → 开流；已不活动/不在作用域 → 关流。
     */
    _reconcile() {
      const rt = this._rt
      const wantedIds = new Set()
      for (const ids of rt.scopes.values()) {
        ids.forEach((id) => wantedIds.add(id))
      }
      for (const id of wantedIds) {
        if (this._isActive(id)) this._acquire(id)
        else this._closeHandle(id)
      }
      for (const id of rt.handles.keys()) {
        if (!wantedIds.has(id)) this._closeHandle(id)
      }
    },

    _applyEvent(id, payload, force) {
      const rt = this._rt
      if (!force && typeof payload.seq === 'number') {
        const last = rt.seq.get(id)
        if (typeof last === 'number' && payload.seq <= last) return // 不倒退
      }
      rt.seq.set(id, payload.seq)

      const idx = this.tasks.findIndex((t) => t.id === id)
      if (idx >= 0) {
        const merged = { ...this.tasks[idx] }
        for (const k of EVENT_FIELDS) {
          if (k in payload) merged[k] = payload[k]
        }
        this.tasks[idx] = merged
      }
      if (this.currentTask?.id === id) {
        const merged = { ...this.currentTask }
        for (const k of EVENT_FIELDS) {
          if (k in payload) merged[k] = payload[k]
        }
        this.currentTask = merged
      }
      this._reconcile()
    },

    // ------------------------------------------------------------------
    // 降级轮询 / 恢复
    // ------------------------------------------------------------------

    _enterFallback() {
      const rt = this._rt
      if (rt.degraded) return
      rt.degraded = true
      this.realtimeDegraded = true
      // 停掉所有尚未自停的流；作用域登记保留，恢复时据此重开
      for (const id of Array.from(rt.handles.keys())) this._closeHandle(id)
      this._schedulePoll(0)
    },

    _onStreamRecovered() {
      const rt = this._rt
      if (!rt.degraded) return
      rt.degraded = false
      this.realtimeDegraded = false
      if (rt.pollTimer) {
        clearTimeout(rt.pollTimer)
        rt.pollTimer = null
      }
      this._reconcile()
    },

    _schedulePoll(delay = POLL_INTERVAL_MS) {
      const rt = this._rt
      if (rt.pollTimer) return
      rt.pollTimer = setTimeout(() => {
        rt.pollTimer = null
        void this._pollTick()
      }, delay)
    },

    async _pollTick() {
      const rt = this._rt
      if (!rt.degraded) return
      if (rt.scopes.has('list')) {
        await this.fetchList()
      }
      const detailIds = rt.scopes.get('detail')
      if (detailIds) {
        for (const id of detailIds) {
          await this.fetchDetail(id).catch(() => {})
        }
      }
      // 快照刷新后尝试重开实时流：任一流首帧到达即整体恢复；
      // 通道仍不可用时 handle 内部自行退避，下一个 tick 再看。
      this._reconcile()
      const stillActive = Array.from(
        new Set([...rt.scopes.get('list') || [], ...rt.scopes.get('detail') || []]),
      ).some((id) => this._isActive(id))
      if (stillActive && rt.degraded) this._schedulePoll()
    },

    // ------------------------------------------------------------------
    // 视图生命周期接口
    // ------------------------------------------------------------------

    /** 列表视图挂载：登记 list 作用域并收敛订阅。 */
    attachList() {
      this._rtInit()
      this._rt.scopes.set('list', new Set())
      this.tasks.forEach((t) => {
        if (ACTIVE_STATUSES.has(t.status)) this._rt.scopes.get('list').add(t.id)
      })
      this._reconcile()
    },

    /** 列表数据刷新/取消操作后：重建 list 作用域的活动任务集合。 */
    refreshListSubscriptions() {
      this._rtInit()
      if (!this._rt.scopes.has('list')) this._rt.scopes.set('list', new Set())
      const ids = this._rt.scopes.get('list')
      this.tasks.forEach((t) => {
        if (ACTIVE_STATUSES.has(t.status)) ids.add(t.id)
      })
      this._reconcile()
    },

    detachList() {
      if (!this._rt) return
      const ids = this._rt.scopes.get('list')
      this._rt.scopes.delete('list')
      if (ids) ids.forEach((id) => {
        if (!this._wantedByScopes(id)) this._closeHandle(id)
      })
      if (this._rt.scopes.size === 0) this._stopAllInternal()
    },

    /** 详情视图挂载：登记 detail 作用域（单任务）。 */
    attachDetail(id) {
      this._rtInit()
      this._rt.scopes.set('detail', new Set([id]))
      this._reconcile()
    },

    detachDetail() {
      if (!this._rt) return
      const ids = this._rt.scopes.get('detail')
      this._rt.scopes.delete('detail')
      if (ids) ids.forEach((id) => {
        if (!this._wantedByScopes(id)) this._closeHandle(id)
      })
      if (this._rt.scopes.size === 0) this._stopAllInternal()
    },

    _stopAllInternal() {
      const rt = this._rt
      for (const id of Array.from(rt.handles.keys())) this._closeHandle(id)
      if (rt.pollTimer) {
        clearTimeout(rt.pollTimer)
        rt.pollTimer = null
      }
      rt.degraded = false
      this.realtimeDegraded = false
      rt.seq.clear()
    },

    /** 取消操作后：确保目标任务仍被订阅（终态帧会自然推送）。 */
    trackTask(id) {
      this._rtInit()
      if (!this._rt.scopes.has('list')) this._rt.scopes.set('list', new Set())
      this._rt.scopes.get('list').add(id)
      this._reconcile()
    },
  },
})
