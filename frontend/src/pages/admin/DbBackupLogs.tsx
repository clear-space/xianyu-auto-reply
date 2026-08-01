/**
 * 数据库备份日志页面
 *
 * 功能：
 * 1. 显示数据库备份任务的执行日志列表（每小时自动备份一次，每次一条记录）
 * 2. 支持按状态、时间范围筛选与分页查询
 * 3. 支持下载备份文件（.sql.gz）
 */
import { useEffect, useState } from 'react'
import {
  Calendar,
  Check,
  ChevronLeft,
  ChevronRight,
  Clock,
  Database,
  Download,
  Edit2,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  Save,
  Settings,
  X,
  Zap,
} from 'lucide-react'

import {
  downloadDbBackupFile,
  getDbBackupLogs,
  type DbBackupLog,
} from '@/api/admin'
import {
  getScheduledTasks,
  triggerScheduledTask,
  updateScheduledTask,
  type ScheduledTask,
} from '@/api/scheduledTasks'
import { getSystemSettings } from '@/api/settings'
import { PageLoading } from '@/components/common/Loading'
import { useAuthStore } from '@/store/authStore'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage, put } from '@/utils/request'

// 备份状态中文映射 + 标签颜色
const STATUS_LABELS: Record<string, { text: string; cls: string }> = {
  success: {
    text: '成功',
    cls: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  },
  failed: {
    text: '失败',
    cls: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  },
}

// 将字节数格式化为易读文本
const formatFileSize = (size: number | null): string => {
  if (size === null || size === undefined) return '-'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(2)} MB`
  return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`
}

// 将耗时（毫秒）格式化为易读文本
const formatDuration = (ms: number | null): string => {
  if (ms === null || ms === undefined) return '-'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

export function DbBackupLogs() {
  const { addToast } = useUIStore()
  const { isAuthenticated, token, _hasHydrated } = useAuthStore()

  const [loading, setLoading] = useState(true)
  const [logs, setLogs] = useState<DbBackupLog[]>([])

  // 时间筛选 - 默认当天
  const today = new Date().toISOString().split('T')[0]
  const [startDate, setStartDate] = useState(today)
  const [endDate, setEndDate] = useState(today)

  // 状态筛选
  const [selectedStatus, setSelectedStatus] = useState('')

  // 下载中的日志ID
  const [downloadingId, setDownloadingId] = useState<number | null>(null)

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

  const loadLogs = async (nextPage: number = currentPage, nextPageSize: number = pageSize) => {
    if (!_hasHydrated || !isAuthenticated || !token) return
    try {
      setLoading(true)
      const result = await getDbBackupLogs({
        page: nextPage,
        pageSize: nextPageSize,
        status: selectedStatus || undefined,
        start_date: startDate || undefined,
        end_date: endDate || undefined,
      })
      if (result.success) {
        setLogs(result.data || [])
        setCurrentPage(nextPage)
        setPageSize(nextPageSize)
        setTotal(result.total || 0)
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
    loadLogs(1, pageSize)
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

  // 分页计算
  const totalPages = Math.ceil(total / pageSize)
  const startIndex = total === 0 ? 0 : (currentPage - 1) * pageSize + 1
  const endIndex = Math.min(currentPage * pageSize, total)

  const handlePageChange = (nextPage: number) => {
    if (nextPage < 1 || nextPage > totalPages) {
      return
    }
    loadLogs(nextPage, pageSize)
  }

  const handlePageSizeChange = (nextPageSize: number) => {
    loadLogs(1, nextPageSize)
  }

  const renderStatus = (status: string) => {
    const meta = STATUS_LABELS[status] || {
      text: status || '-',
      cls: 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300',
    }
    return (
      <span className={`inline-block text-xs px-2 py-1 rounded whitespace-nowrap ${meta.cls}`}>
        {meta.text}
      </span>
    )
  }

  if (loading && logs.length === 0) {
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
            <button onClick={handleSearch} className="btn-ios-primary">
              <Calendar className="w-4 h-4" />
              查询
            </button>
          </div>
        </div>
      </div>

      {/* Logs List */}
      <div
        className="vben-card flex flex-col"
        style={{ height: 'calc(100vh - 480px)', minHeight: '400px' }}
      >
        <div className="vben-card-header flex-shrink-0">
          <h2 className="vben-card-title">
            <Database className="w-4 h-4 text-blue-500" />
            备份执行记录
          </h2>
          <span className="badge-primary">{total} 条记录</span>
        </div>
        <div className="flex-1 overflow-x-auto overflow-y-auto">
          <table className="table-ios">
            <thead className="sticky top-0 bg-white dark:bg-slate-800 z-10">
              <tr>
                <th className="min-w-[100px]">状态</th>
                <th className="min-w-[280px]">备份文件名</th>
                <th className="min-w-[100px]">文件大小</th>
                <th className="min-w-[80px]">表数量</th>
                <th className="min-w-[100px]">数据行数</th>
                <th className="min-w-[90px]">耗时</th>
                <th className="min-w-[250px]">错误详情</th>
                <th className="min-w-[155px]">备份时间</th>
                <th className="min-w-[100px]">操作</th>
              </tr>
            </thead>
            <tbody>
              {logs.length === 0 ? (
                <tr>
                  <td colSpan={9} className="text-center py-8 text-slate-500 dark:text-slate-400">
                    <div className="flex flex-col items-center gap-2">
                      <Database className="w-12 h-12 text-slate-300 dark:text-slate-600" />
                      <p>暂无数据库备份日志</p>
                    </div>
                  </td>
                </tr>
              ) : (
                logs.map((log) => (
                  <tr key={log.id}>
                    <td className="whitespace-nowrap">{renderStatus(log.status)}</td>
                    <td className="font-medium text-blue-600 dark:text-blue-400 max-w-[300px]">
                      <span className="block truncate" title={log.file_name || ''}>
                        {log.file_name || '-'}
                      </span>
                    </td>
                    <td className="text-slate-600 dark:text-slate-300 whitespace-nowrap">
                      {formatFileSize(log.file_size)}
                    </td>
                    <td className="text-slate-600 dark:text-slate-300">
                      {log.table_count ?? '-'}
                    </td>
                    <td className="text-slate-600 dark:text-slate-300">
                      {log.total_rows ?? '-'}
                    </td>
                    <td className="text-slate-500 dark:text-slate-400 whitespace-nowrap">
                      {formatDuration(log.duration_ms)}
                    </td>
                    <td className="max-w-[260px] text-slate-500 dark:text-slate-400">
                      <span className="block truncate cursor-help" title={log.error_message || ''}>
                        {log.error_message || '-'}
                      </span>
                    </td>
                    <td className="text-slate-500 dark:text-slate-400 text-sm whitespace-nowrap">
                      {new Date(log.created_at).toLocaleString()}
                    </td>
                    <td className="whitespace-nowrap">
                      {log.downloadable ? (
                        <button
                          onClick={() => handleDownload(log)}
                          disabled={downloadingId === log.id}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors bg-blue-50 text-blue-600 hover:bg-blue-100 dark:bg-blue-900/20 dark:text-blue-400 dark:hover:bg-blue-900/30 disabled:opacity-50 disabled:cursor-not-allowed"
                          title="下载备份文件"
                        >
                          {downloadingId === log.id ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            <Download className="w-4 h-4" />
                          )}
                          下载
                        </button>
                      ) : (
                        <span className="text-xs text-slate-400 dark:text-slate-500">不可下载</span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* 分页 */}
        {total > 0 && (
          <div className="flex-shrink-0 vben-card-footer flex flex-col sm:flex-row items-center justify-between gap-4 px-4 py-3 border-t border-slate-200 dark:border-slate-700">
            <div className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
              <span>每页</span>
              <select
                value={pageSize}
                onChange={(e) => handlePageSizeChange(Number(e.target.value))}
                className="px-2 py-1 border border-slate-300 dark:border-slate-600 rounded bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300"
              >
                <option value={10}>10</option>
                <option value={20}>20</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
              </select>
              <span>条</span>
              <span className="ml-2">
                显示 {startIndex}-{endIndex} 条，共 {total} 条
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => handlePageChange(currentPage - 1)}
                disabled={currentPage === 1}
                className="p-2 rounded border border-slate-300 dark:border-slate-600 disabled:opacity-50 disabled:cursor-not-allowed hover:bg-slate-100 dark:hover:bg-slate-700"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="px-3 py-1 text-sm text-slate-600 dark:text-slate-400">
                第 {currentPage} / {totalPages || 1} 页
              </span>
              <button
                onClick={() => handlePageChange(currentPage + 1)}
                disabled={currentPage >= totalPages}
                className="p-2 rounded border border-slate-300 dark:border-slate-600 disabled:opacity-50 disabled:cursor-not-allowed hover:bg-slate-100 dark:hover:bg-slate-700"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
