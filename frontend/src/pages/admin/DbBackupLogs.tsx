/**
 * 数据库备份日志页面
 *
 * 功能：
 * 1. 显示数据库备份任务的执行日志列表（每小时自动备份一次，每次一条记录）
 * 2. 支持按状态、时间范围筛选与分页查询
 * 3. 支持下载备份文件（.sql.gz）
 * 4. 支持删除备份日志（关联的备份文件一并删除）
 */
import { useEffect, useRef, useState } from 'react'
import {
  Calendar,
  Check,
  Clock,
  Database,
  Edit2,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Settings,
  Trash2,
  X,
  Zap,
} from 'lucide-react'

import {
  batchDeleteDbBackupLogs,
  deleteDbBackupLog,
  downloadDbBackupFile,
  getDbBackupLogs,
  type DbBackupLog,
} from '@/api/dbBackupLogs'
import {
  getScheduledTasks,
  triggerScheduledTask,
  updateScheduledTask,
  type ScheduledTask,
} from '@/api/scheduledTasks'
import { getSystemSettings } from '@/api/settings'
import { ConfirmModal } from '@/components/common/ConfirmModal'
import { PageLoading } from '@/components/common/Loading'
import { Pager } from '@/components/common/Pager'
import { DbBackupLogsTable } from './DbBackupLogsTable'
import { useAuthStore } from '@/store/authStore'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage, put } from '@/utils/request'
import { getBeijingDateInputValue } from '@/utils/date'

interface DbBackupLogFilters {
  startDate: string
  endDate: string
  status: string
}

// 跑马灯文本：内容超出容器宽度时横向无缝滚动（用于弹窗中展示长文件名）
function MarqueeText({ text }: { text: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [scrolling, setScrolling] = useState(false)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    setScrolling(el.scrollWidth > el.clientWidth)
  }, [text])

  return (
    <div ref={containerRef} className="w-full overflow-hidden">
      <div className={`inline-flex whitespace-nowrap ${scrolling ? 'animate-marquee' : ''}`}>
        <span className="pr-8 font-mono text-blue-600 dark:text-blue-400">{text}</span>
        {scrolling && (
          <span className="pr-8 font-mono text-blue-600 dark:text-blue-400" aria-hidden="true">
            {text}
          </span>
        )}
      </div>
    </div>
  )
}

export function DbBackupLogs() {
  const { addToast } = useUIStore()
  const { isAuthenticated, token, _hasHydrated } = useAuthStore()

  const [loading, setLoading] = useState(true)
  const [logs, setLogs] = useState<DbBackupLog[]>([])

  // 时间筛选 - 默认当天
  const today = getBeijingDateInputValue()
  const [startDate, setStartDate] = useState(today)
  const [endDate, setEndDate] = useState(today)

  // 状态筛选
  const [selectedStatus, setSelectedStatus] = useState('')
  const [appliedFilters, setAppliedFilters] = useState<DbBackupLogFilters>({
    startDate: today,
    endDate: today,
    status: '',
  })

  // 下载中的日志ID
  const [downloadingId, setDownloadingId] = useState<number | null>(null)

  // 删除确认弹窗
  const [deleteConfirm, setDeleteConfirm] = useState<{ open: boolean; log: DbBackupLog | null }>({
    open: false,
    log: null,
  })
  const [deleting, setDeleting] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  // 确认弹窗持有打开时的 ID 快照，列表刷新不会改变本次确认的删除范围。
  const [batchDeleteIds, setBatchDeleteIds] = useState<number[] | null>(null)
  const [batchDeleting, setBatchDeleting] = useState(false)

  // 分页
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [total, setTotal] = useState(0)

  // ========== 备份配置 ==========
  // 自动备份任务配置（来自 xy_scheduled_tasks）
  const [taskConfig, setTaskConfig] = useState<ScheduledTask | null>(null)
  const [taskConfigLoading, setTaskConfigLoading] = useState(true)

  // 备份保留天数（来自 xy_system_settings）
  const [retentionDays, setRetentionDays] = useState(10)
  const [retentionDaysLoading, setRetentionDaysLoading] = useState(true)

  // 编辑状态
  const [editingInterval, setEditingInterval] = useState(false)
  const [editIntervalValue, setEditIntervalValue] = useState(0)
  const [editingRetention, setEditingRetention] = useState(false)
  const [editRetentionValue, setEditRetentionValue] = useState(10)

  // 按钮加载状态
  const [savingConfig, setSavingConfig] = useState<string | null>(null)
  const [triggeringBackup, setTriggeringBackup] = useState(false)

  const loadLogs = async (
    nextPage: number = currentPage,
    nextPageSize: number = pageSize,
    filters: DbBackupLogFilters = appliedFilters,
  ) => {
    if (!_hasHydrated || !isAuthenticated || !token) return
    try {
      setLoading(true)
      setSelectedIds(new Set())
      const result = await getDbBackupLogs({
        page: nextPage,
        pageSize: nextPageSize,
        status: filters.status || undefined,
        start_date: filters.startDate || undefined,
        end_date: filters.endDate || undefined,
      })
      if (result.success) {
        const nextLogs = result.data || []
        const nextTotal = result.total || 0
        const lastPage = Math.max(1, Math.ceil(nextTotal / nextPageSize))
        if (nextLogs.length === 0 && nextTotal > 0 && nextPage > lastPage) {
          await loadLogs(lastPage, nextPageSize, filters)
          return
        }
        setLogs(nextLogs)
        setCurrentPage(nextPage)
        setPageSize(nextPageSize)
        setTotal(nextTotal)
      } else {
        setLogs([])
        setCurrentPage(nextPage)
        setPageSize(nextPageSize)
        setTotal(0)
        addToast({ type: 'error', message: result.message || '加载数据库备份日志失败' })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '加载数据库备份日志失败') })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!_hasHydrated || !isAuthenticated || !token) return
    loadLogs(1, pageSize)
    loadTaskConfig()
    loadRetentionDays()
    // 仅在认证态变更时初始化加载
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [_hasHydrated, isAuthenticated, token])

  // ========== 备份配置加载 ==========

  const loadTaskConfig = async () => {
    try {
      const result = await getScheduledTasks()
      if (result.success && result.data) {
        const dbBackup = result.data.find((t) => t.task_code === 'db_backup')
        if (dbBackup) setTaskConfig(dbBackup)
      }
    } catch {
      // 静默失败
    } finally {
      setTaskConfigLoading(false)
    }
  }

  const loadRetentionDays = async () => {
    try {
      const { data } = await getSystemSettings()
      if (data) {
        const raw = (data as Record<string, unknown>)['db_backup.retention_days']
        const days = raw !== undefined && raw !== null ? Number(raw) : 10
        setRetentionDays(days >= 1 && days <= 365 ? days : 10)
      }
    } catch {
      // 静默失败
    } finally {
      setRetentionDaysLoading(false)
    }
  }

  // ========== 备份配置操作 ==========

  const handleTriggerBackup = async () => {
    setTriggeringBackup(true)
    try {
      const result = await triggerScheduledTask('db_backup')
      if (result.success) {
        addToast({ type: 'success', message: result.message || '备份任务已触发，正在执行...' })
        // 轮询刷新日志列表，直到新记录出现或超时
        // 备份通常需要 5-30 秒，每 3 秒检查一次，最多 15 次（45秒）
        let pollCount = 0
        const maxPolls = 15
        const interval = setInterval(async () => {
          pollCount++
          await loadLogs(1, pageSize)
          if (pollCount >= maxPolls) {
            clearInterval(interval)
          }
        }, 3000)
        // 组件卸载时清理
        setTimeout(() => clearInterval(interval), maxPolls * 3000 + 1000)
      } else {
        addToast({ type: 'error', message: result.message || '触发备份失败' })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '触发备份失败') })
    } finally {
      setTriggeringBackup(false)
    }
  }

  const handleToggleEnabled = async () => {
    if (!taskConfig) return
    setSavingConfig('enabled')
    try {
      const result = await updateScheduledTask('db_backup', { enabled: !taskConfig.enabled })
      if (result.success) {
        addToast({ type: 'success', message: result.message })
        loadTaskConfig()
      } else {
        addToast({ type: 'error', message: result.message || '更新失败' })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '更新失败') })
    } finally {
      setSavingConfig(null)
    }
  }

  const handleStartEditInterval = () => {
    setEditIntervalValue(taskConfig?.interval_seconds ?? 3600)
    setEditingInterval(true)
  }

  const handleCancelEditInterval = () => {
    setEditingInterval(false)
  }

  const handleSaveInterval = async () => {
    if (editIntervalValue < 1) {
      addToast({ type: 'error', message: '间隔时间不能小于1秒' })
      return
    }
    setSavingConfig('interval')
    try {
      const result = await updateScheduledTask('db_backup', { interval_seconds: editIntervalValue })
      if (result.success) {
        addToast({ type: 'success', message: result.message })
        setEditingInterval(false)
        loadTaskConfig()
      } else {
        addToast({ type: 'error', message: result.message || '更新失败' })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '更新失败') })
    } finally {
      setSavingConfig(null)
    }
  }

  const handleStartEditRetention = () => {
    setEditRetentionValue(retentionDays)
    setEditingRetention(true)
  }

  const handleCancelEditRetention = () => {
    setEditingRetention(false)
  }

  const handleSaveRetention = async () => {
    if (editRetentionValue < 1 || editRetentionValue > 365) {
      addToast({ type: 'error', message: '保留天数必须在 1 到 365 之间' })
      return
    }
    setSavingConfig('retention')
    try {
      const response = await put<{ success: boolean; message?: string }>(
        '/api/v1/system-settings/db_backup.retention_days',
        { value: String(editRetentionValue) },
      )
      if (response.success) {
        addToast({ type: 'success', message: '备份保留天数已更新' })
        setRetentionDays(editRetentionValue)
        setEditingRetention(false)
      } else {
        addToast({ type: 'error', message: response.message || '更新失败' })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '更新失败') })
    } finally {
      setSavingConfig(null)
    }
  }

  const handleSearch = () => {
    if (startDate && endDate && startDate > endDate) {
      addToast({ type: 'error', message: '开始日期不能晚于结束日期' })
      return
    }
    const filters = { startDate, endDate, status: selectedStatus }
    setAppliedFilters(filters)
    loadLogs(1, pageSize, filters)
  }

  const handleReset = () => {
    const filters = { startDate: today, endDate: today, status: '' }
    setStartDate(today)
    setEndDate(today)
    setSelectedStatus('')
    setAppliedFilters(filters)
    loadLogs(1, pageSize, filters)
  }

  const handleDownload = async (log: DbBackupLog) => {
    if (!log.downloadable) return
    setDownloadingId(log.id)
    try {
      const result = await downloadDbBackupFile(log.id)
      if (!result.success || !result.blob) {
        addToast({ type: 'error', message: result.message || '下载失败' })
        return
      }
      // 触发浏览器下载
      const url = window.URL.createObjectURL(result.blob)
      const link = document.createElement('a')
      link.href = url
      link.download = result.filename || log.file_name || 'backup.sql.gz'
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(url)
      addToast({ type: 'success', message: '备份文件下载已开始' })
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '下载失败') })
    } finally {
      setDownloadingId(null)
    }
  }

  // 删除备份日志（日志记录 + 关联备份文件）
  const handleDelete = async () => {
    const log = deleteConfirm.log
    if (!log) return
    setDeleting(true)
    try {
      const result = await deleteDbBackupLog(log.id)
      if (result.success) {
        addToast({ type: 'success', message: result.message || '备份日志已删除' })
      } else {
        addToast({ type: 'error', message: result.message || '删除失败' })
      }
      const recordRemoved = result.success || result.code === 40002
      if (recordRemoved) {
        setDeleteConfirm({ open: false, log: null })
        const nextPage = logs.length === 1 && currentPage > 1 ? currentPage - 1 : currentPage
        await loadLogs(nextPage, pageSize)
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '删除失败') })
    } finally {
      setDeleting(false)
    }
  }

  const handleBatchDelete = async () => {
    const ids = batchDeleteIds
    if (!ids?.length) {
      addToast({ type: 'error', message: '请选择要删除的备份日志' })
      return
    }
    setBatchDeleting(true)
    try {
      const result = await batchDeleteDbBackupLogs(ids)
      if (result.success) {
        addToast({ type: 'success', message: result.message || `成功删除 ${ids.length} 条备份日志` })
      } else {
        addToast({ type: 'error', message: result.message || '批量删除失败' })
      }
      const recordsRemoved = result.success || result.code === 40002
      if (recordsRemoved) {
        setBatchDeleteIds(null)
        setSelectedIds(new Set())
        // 全部删除成功时直接回退；部分成功时由 loadLogs 根据新的总数校正页码。
        const nextPage = result.success && logs.length === ids.length && currentPage > 1
          ? currentPage - 1
          : currentPage
        await loadLogs(nextPage, pageSize)
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '批量删除失败') })
    } finally {
      setBatchDeleting(false)
    }
  }

  const toggleSelectAll = () => {
    // 只切换当前页全选，避免误选其他页的数据。
    if (logs.length > 0 && logs.every((log) => selectedIds.has(log.id))) {
      setSelectedIds((prev) => {
        const next = new Set(prev)
        logs.forEach((log) => next.delete(log.id))
        return next
      })
      return
    }
    setSelectedIds((prev) => {
      const next = new Set(prev)
      logs.forEach((log) => next.add(log.id))
      return next
    })
  }

  // 分页计算
  const totalPages = Math.ceil(total / pageSize)
  const hasChangedFilters =
    startDate !== today ||
    endDate !== today ||
    selectedStatus !== '' ||
    appliedFilters.startDate !== today ||
    appliedFilters.endDate !== today ||
    appliedFilters.status !== ''

  const handlePageChange = (nextPage: number) => {
    if (nextPage < 1 || nextPage > totalPages) {
      return
    }
    loadLogs(nextPage, pageSize)
  }

  const handlePageSizeChange = (nextPageSize: number) => {
    loadLogs(1, nextPageSize)
  }

  if (loading && logs.length === 0 && !deleteConfirm.open && !batchDeleteIds) {
    return <PageLoading />
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="page-title">数据库备份日志</h1>
          <p className="page-description">查看数据库自动备份的执行结果，支持下载备份文件</p>
        </div>
        <div className="flex gap-3 flex-wrap">
          {selectedIds.size > 0 && (
            <button
              onClick={() => setBatchDeleteIds(Array.from(selectedIds))}
              disabled={loading || batchDeleting}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors bg-red-50 text-red-600 hover:bg-red-100 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/30 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Trash2 className="w-4 h-4" />
              删除选中（{selectedIds.size}）
            </button>
          )}
          <button onClick={() => loadLogs()} disabled={loading} className="btn-ios-secondary">
            {loading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            刷新
          </button>
        </div>
      </div>

      {/* Backup Config Card */}
      <div className="vben-card">
        <div className="vben-card-header">
          <h2 className="vben-card-title">
            <Settings className="w-4 h-4 text-blue-500" />
            备份配置
          </h2>
        </div>
        <div className="vben-card-body">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {/* 手动备份 */}
            <div className="flex flex-col gap-2">
              <span className="text-sm text-slate-500 dark:text-slate-400">手动备份</span>
              <button
                onClick={handleTriggerBackup}
                disabled={triggeringBackup}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-blue-50 text-blue-600 hover:bg-blue-100 dark:bg-blue-900/20 dark:text-blue-400 dark:hover:bg-blue-900/30 transition-colors disabled:opacity-50"
              >
                {triggeringBackup ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Zap className="w-4 h-4" />
                )}
                立即备份
              </button>
              <span className="text-xs text-slate-400 dark:text-slate-500">
                点击立即执行一次数据库备份
              </span>
            </div>

            {/* 执行间隔 */}
            <div className="flex flex-col gap-2">
              <span className="text-sm text-slate-500 dark:text-slate-400">
                <Clock className="w-3.5 h-3.5 inline mr-1" />
                执行间隔
              </span>
              {taskConfigLoading ? (
                <span className="text-slate-400 text-sm">加载中...</span>
              ) : editingInterval ? (
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min="1"
                    value={editIntervalValue}
                    onChange={(e) => setEditIntervalValue(Number(e.target.value))}
                    className="w-24 px-2 py-1 border border-slate-300 dark:border-slate-600 rounded bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 text-sm"
                  />
                  <span className="text-sm text-slate-500">秒</span>
                  <button
                    onClick={handleSaveInterval}
                    disabled={savingConfig === 'interval'}
                    className="p-1 rounded hover:bg-green-50 dark:hover:bg-green-900/20"
                    title="保存"
                  >
                    {savingConfig === 'interval' ? (
                      <Loader2 className="w-4 h-4 animate-spin text-green-500" />
                    ) : (
                      <Check className="w-4 h-4 text-green-500" />
                    )}
                  </button>
                  <button
                    onClick={handleCancelEditInterval}
                    className="p-1 rounded hover:bg-red-50 dark:hover:bg-red-900/20"
                    title="取消"
                  >
                    <X className="w-4 h-4 text-red-500" />
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <span className="font-medium text-slate-900 dark:text-white">
                    {taskConfig ? taskConfig.interval_seconds : '-'}
                  </span>
                  <span className="text-sm text-slate-500">秒</span>
                  {taskConfig && (
                    <button
                      onClick={handleStartEditInterval}
                      className="p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700"
                      title="编辑间隔"
                    >
                      <Edit2 className="w-3.5 h-3.5 text-slate-400" />
                    </button>
                  )}
                </div>
              )}
              <span className="text-xs text-slate-400 dark:text-slate-500">
                自动备份的执行间隔
              </span>
            </div>

            {/* 启用/禁用 */}
            <div className="flex flex-col gap-2">
              <span className="text-sm text-slate-500 dark:text-slate-400">自动备份</span>
              {taskConfigLoading ? (
                <span className="text-slate-400 text-sm">加载中...</span>
              ) : taskConfig ? (
                <div className="flex items-center gap-3">
                  <span
                    className={`text-sm font-medium px-2.5 py-1 rounded ${
                      taskConfig.enabled
                        ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                        : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400'
                    }`}
                  >
                    {taskConfig.enabled ? '已启用' : '已禁用'}
                  </span>
                  <button
                    onClick={handleToggleEnabled}
                    disabled={savingConfig === 'enabled'}
                    className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                      taskConfig.enabled
                        ? 'bg-red-50 text-red-600 hover:bg-red-100 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/30'
                        : 'bg-green-50 text-green-600 hover:bg-green-100 dark:bg-green-900/20 dark:text-green-400 dark:hover:bg-green-900/30'
                    }`}
                  >
                    {savingConfig === 'enabled' ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : taskConfig.enabled ? (
                      <Pause className="w-4 h-4" />
                    ) : (
                      <Play className="w-4 h-4" />
                    )}
                    {taskConfig.enabled ? '禁用' : '启用'}
                  </button>
                </div>
              ) : (
                <span className="text-slate-400 text-sm">不可用</span>
              )}
              <span className="text-xs text-slate-400 dark:text-slate-500">
                {taskConfig?.enabled ? '定时备份正在运行' : '定时备份已暂停'}
              </span>
            </div>

            {/* 保留天数 */}
            <div className="flex flex-col gap-2">
              <span className="text-sm text-slate-500 dark:text-slate-400">保留天数</span>
              {retentionDaysLoading ? (
                <span className="text-slate-400 text-sm">加载中...</span>
              ) : editingRetention ? (
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min="1"
                    max="365"
                    value={editRetentionValue}
                    onChange={(e) => setEditRetentionValue(Number(e.target.value))}
                    className="w-24 px-2 py-1 border border-slate-300 dark:border-slate-600 rounded bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 text-sm"
                  />
                  <span className="text-sm text-slate-500">天</span>
                  <button
                    onClick={handleSaveRetention}
                    disabled={savingConfig === 'retention'}
                    className="p-1 rounded hover:bg-green-50 dark:hover:bg-green-900/20"
                    title="保存"
                  >
                    {savingConfig === 'retention' ? (
                      <Loader2 className="w-4 h-4 animate-spin text-green-500" />
                    ) : (
                      <Check className="w-4 h-4 text-green-500" />
                    )}
                  </button>
                  <button
                    onClick={handleCancelEditRetention}
                    className="p-1 rounded hover:bg-red-50 dark:hover:bg-red-900/20"
                    title="取消"
                  >
                    <X className="w-4 h-4 text-red-500" />
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <span className="font-medium text-slate-900 dark:text-white">{retentionDays}</span>
                  <span className="text-sm text-slate-500">天</span>
                  <button
                    onClick={handleStartEditRetention}
                    className="p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700"
                    title="编辑保留天数"
                  >
                    <Edit2 className="w-3.5 h-3.5 text-slate-400" />
                  </button>
                </div>
              )}
              <span className="text-xs text-slate-400 dark:text-slate-500">
                备份文件保留 {retentionDays} 天后自动清理
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Filter */}
      <div className="vben-card">
        <div className="vben-card-body">
          <div className="flex flex-wrap items-end gap-4">
            <div className="input-group">
              <label className="input-label">开始日期</label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="input-ios"
              />
            </div>
            <div className="input-group">
              <label className="input-label">结束日期</label>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="input-ios"
              />
            </div>
            <div className="input-group">
              <label className="input-label">备份状态</label>
              <select
                value={selectedStatus}
                onChange={(e) => setSelectedStatus(e.target.value)}
                className="input-ios"
              >
                <option value="">全部状态</option>
                <option value="success">成功</option>
                <option value="failed">失败</option>
              </select>
            </div>
            <div className="ml-auto flex items-center gap-3">
              {hasChangedFilters && (
                <button onClick={handleReset} disabled={loading} className="btn-ios-secondary">
                  <RotateCcw className="w-4 h-4" />
                  重置
                </button>
              )}
              <button onClick={handleSearch} disabled={loading} className="btn-ios-primary">
                <Calendar className="w-4 h-4" />
                查询
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Logs List */}
      <div
        className="vben-card relative flex flex-col overflow-hidden"
        style={{ height: 'calc(100vh - 480px)', minHeight: '400px' }}
      >
        {loading && (
          <div className="absolute inset-0 z-30 flex items-center justify-center bg-white/70 backdrop-blur-sm dark:bg-slate-900/70">
            <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
              <Loader2 className="w-5 h-5 animate-spin text-blue-500" />
              加载中...
            </div>
          </div>
        )}
        <div className="vben-card-header flex-shrink-0">
          <h2 className="vben-card-title">
            <Database className="w-4 h-4 text-blue-500" />
            备份执行记录
          </h2>
          <span className="badge-primary">{total} 条记录</span>
        </div>
        <DbBackupLogsTable
          logs={logs}
          selectedIds={selectedIds}
          downloadingId={downloadingId}
          onToggleAll={toggleSelectAll}
          onToggleOne={(logId) =>
            setSelectedIds((prev) => {
              const next = new Set(prev)
              if (next.has(logId)) next.delete(logId)
              else next.add(logId)
              return next
            })
          }
          onDownload={handleDownload}
          onDelete={(log) => setDeleteConfirm({ open: true, log })}
        />

        {/* 分页 */}
        {total > 0 && (
          <Pager
            page={currentPage}
            pageSize={pageSize}
            total={total}
            totalPages={totalPages}
            loading={loading}
            onPageChange={handlePageChange}
            onPageSizeChange={handlePageSizeChange}
          />
        )}
      </div>

      {/* 删除确认弹窗（沿用删除卡券的弹窗风格） */}
      <ConfirmModal
        isOpen={deleteConfirm.open}
        type="danger"
        title="删除备份日志"
        confirmText="删除"
        loading={deleting}
        message={
          <div className="space-y-1.5">
            <div>确认要删除备份日志</div>
            <MarqueeText text={deleteConfirm.log?.file_name || '-'} />
            <div className="text-amber-500 dark:text-amber-400">
              该记录关联的备份文件也会一并删除
            </div>
            <div className="font-bold text-red-500 dark:text-red-400">此操作不可恢复</div>
          </div>
        }
        onConfirm={handleDelete}
        onCancel={() => setDeleteConfirm({ open: false, log: null })}
      />

      {/* 批量删除确认弹窗 */}
      <ConfirmModal
        isOpen={batchDeleteIds !== null}
        type="danger"
        title="批量删除备份日志"
        confirmText="批量删除"
        loading={batchDeleting}
        message={`确认删除选中的 ${batchDeleteIds?.length ?? 0} 条备份日志及其关联文件吗？此操作不可恢复。`}
        onConfirm={handleBatchDelete}
        onCancel={() => { if (!batchDeleting) setBatchDeleteIds(null) }}
      />
    </div>
  )
}
