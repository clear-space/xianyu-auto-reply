import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  Cpu,
  Database,
  HardDrive,
  Loader2,
  MemoryStick,
  RefreshCw,
  Server,
  Sparkles,
  Trash2,
  Wifi,
} from 'lucide-react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  ackSystemAlert,
  getCleanupReport,
  getSystemAlerts,
  getSystemMetrics,
  getSystemStorage,
  getSystemSummary,
  getSystemTables,
  refreshDirs,
  type BackupFileInfo,
  type CleanupReport,
  type DirSizeInfo,
  type HostMetrics,
  type MetricPoint,
  type RetentionPolicyItem,
  type SystemSummary,
  type TableRankItem,
} from '@/api/systemInfo'
import { triggerScheduledTask } from '@/api/scheduledTasks'
import { clearSystemLogs } from '@/api/admin'
import { useUIStore } from '@/store/uiStore'
import { useAuthStore } from '@/store/authStore'
import { PageLoading } from '@/components/common/Loading'
import { ConfirmModal } from '@/components/common/ConfirmModal'
import { CleanupModal } from './CleanupModal'

// ==================== 工具函数 ====================

function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex++
  }
  return `${value.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`
}

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds < 0) return '-'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days}天${hours}小时${minutes}分`
  if (hours > 0) return `${hours}小时${minutes}分`
  return `${minutes}分钟`
}

function formatRate(bytesPerSecond: number): string {
  if (!bytesPerSecond) return '0 B/s'
  const units = ['B/s', 'KB/s', 'MB/s', 'GB/s']
  let value = bytesPerSecond
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex++
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`
}

function ringStyle(percent: number, color: string): React.CSSProperties {
  const clamped = Math.max(0, Math.min(100, percent))
  return {
    background: `conic-gradient(${color} ${clamped * 3.6}deg, rgba(148,163,184,0.25) 0deg)`,
  }
}

const PIE_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ef4444', '#06b6d4', '#84cc16', '#f97316']

// ==================== 小组件 ====================

function RingGauge({ percent, label, color }: { percent: number | null; label: string; color: string }) {
  const value = percent ?? 0
  return (
    <div className="flex flex-col items-center gap-1">
      <div className="relative w-16 h-16 rounded-full" style={ringStyle(value, color)}>
        <div className="absolute inset-1.5 rounded-full bg-white dark:bg-gray-800 flex items-center justify-center">
          <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">
            {percent === null ? '-' : `${value.toFixed(0)}%`}
          </span>
        </div>
      </div>
      <span className="text-xs text-slate-500 dark:text-slate-400">{label}</span>
    </div>
  )
}

function ServiceLight({ name, available }: { name: string; available: boolean | null | undefined }) {
  const isUnknown = available === null || available === undefined
  const color = isUnknown ? 'bg-gray-300' : available ? 'bg-emerald-500' : 'bg-red-500'
  const label = isUnknown ? '未知' : available ? '在线' : '离线'
  return (
    <div className="flex items-center gap-2">
      <span className={`w-2.5 h-2.5 rounded-full ${color}`} />
      <span className="text-sm text-slate-700 dark:text-slate-300">{name}</span>
      <span className={`text-xs ${isUnknown ? 'text-gray-400' : available ? 'text-emerald-600' : 'text-red-500'}`}>
        {label}
      </span>
    </div>
  )
}

// ==================== 主页面 ====================

export function SystemInfo() {
  const { addToast } = useUIStore()
  const { isAuthenticated, token, _hasHydrated } = useAuthStore()

  const [loading, setLoading] = useState(true)
  const [refreshInterval, setRefreshInterval] = useState(30)
  const [summary, setSummary] = useState<SystemSummary | null>(null)
  const [storage, setStorage] = useState<{ dirs: Record<string, DirSizeInfo>; backups: BackupFileInfo[] } | null>(null)
  const [tables, setTables] = useState<{
    db_total_size: number
    top_tables: TableRankItem[]
    retention_policies: RetentionPolicyItem[]
  } | null>(null)
  const [points, setPoints] = useState<MetricPoint[]>([])
  const [rangeHours, setRangeHours] = useState(24)
  const [alerts, setAlerts] = useState<Array<{ id: number; alert_type: string; level: string; title: string; detail: Record<string, unknown> | null; created_at: string | null; status: string; source: string; resolved_at: string | null }>>([])
  const [alertFilter, setAlertFilter] = useState('active')
  const [collecting, setCollecting] = useState(false)

  // 手动清理相关状态
  const [cleanupOpen, setCleanupOpen] = useState(false)
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [cleanupReport, setCleanupReport] = useState<CleanupReport | null>(null)
  const [confirmDialog, setConfirmDialog] = useState<{
    title: string
    message: string
    confirmText: string
    type: 'warning' | 'danger' | 'info'
  } | null>(null)
  const confirmResolverRef = useRef<((value: boolean) => void) | null>(null)

  const requestConfirm = useCallback((options: {
    title?: string
    message: string
    confirmText?: string
    type?: 'warning' | 'danger' | 'info'
  }) => new Promise<boolean>((resolve) => {
    confirmResolverRef.current?.(false)
    confirmResolverRef.current = resolve
    setConfirmDialog({
      title: options.title || '确认操作',
      message: options.message,
      confirmText: options.confirmText || '确定',
      type: options.type || 'warning',
    })
  }), [])

  const closeConfirm = (confirmed: boolean) => {
    confirmResolverRef.current?.(confirmed)
    confirmResolverRef.current = null
    setConfirmDialog(null)
  }

  const loadAll = useCallback(async () => {
    try {
      const [summaryData, storageData, tablesData, reportData] = await Promise.all([
        getSystemSummary(),
        getSystemStorage(),
        getSystemTables(),
        getCleanupReport().catch(() => null),
      ])
      setSummary(summaryData)
      setStorage(storageData)
      setTables(tablesData)
      if (reportData) setCleanupReport(reportData)
    } catch (e: any) {
      addToast({ type: 'error', message: e.message || '加载系统信息失败' })
    } finally {
      setLoading(false)
    }
  }, [addToast])

  const loadTrend = useCallback(async () => {
    try {
      setPoints(await getSystemMetrics(rangeHours))
    } catch {
      // 趋势数据失败静默（首次运行无历史数据属正常）
    }
  }, [rangeHours])

  const loadAlerts = useCallback(async () => {
    try {
      setAlerts(await getSystemAlerts(alertFilter))
    } catch (e: any) {
      addToast({ type: 'error', message: e.message || '加载告警列表失败' })
    }
  }, [alertFilter, addToast])

  useEffect(() => {
    if (!_hasHydrated || !isAuthenticated || !token) return
    loadAll()
    loadTrend()
    loadAlerts()
  }, [_hasHydrated, isAuthenticated, token, loadAll, loadTrend, loadAlerts])

  // 定时刷新
  useEffect(() => {
    if (!_hasHydrated || !isAuthenticated || !token) return
    if (refreshInterval <= 0) return
    const timer = window.setInterval(() => {
      loadAll()
      loadTrend()
      loadAlerts()
    }, refreshInterval * 1000)
    return () => window.clearInterval(timer)
  }, [_hasHydrated, isAuthenticated, token, refreshInterval, loadAll, loadTrend, loadAlerts])

  useEffect(() => {
    if (_hasHydrated && isAuthenticated && token) loadTrend()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeHours])

  const handleManualCollect = async () => {
    setCollecting(true)
    try {
      const result = await triggerScheduledTask('system_metrics_collect')
      if (result.success) {
        addToast({ type: 'success', message: '采集任务已触发，几秒后刷新可见最新数据' })
        setTimeout(() => { loadAll(); loadTrend(); loadAlerts() }, 3000)
      } else {
        addToast({ type: 'error', message: result.message || '触发采集失败' })
      }
    } catch {
      addToast({ type: 'error', message: '触发采集失败，请检查调度服务是否在线' })
    } finally {
      setCollecting(false)
    }
  }

  // ========== 单类清理 ==========
  // 存储分类 → 清理动作映射
  const SINGLE_CLEANUP_TASKS: Record<string, string> = {
    db: 'data_retention_cleanup',
    files: 'image_cleanup',
    browser: 'cleanup_browser_data',
    backup: 'db_backup',
  }
  const SINGLE_CLEANUP_CONFIRM: Record<string, { title: string; message: string }> = {
    db: {
      title: '清理数据库日志',
      message: '将按保留策略删除超期日志数据（默认30天），只删超期数据、不动活跃数据，是否继续？',
    },
    files: {
      title: '清理上传文件',
      message: '将清理卡券/素材/关键词/回复/人脸截图/公开媒体中的孤儿与超期图片（有引用与未到期文件会保留），是否继续？',
    },
    browser: {
      title: '清理浏览器数据',
      message: '仅清理「禁用超过10天」账号的浏览器数据，启用账号的登录态会保留，是否继续？',
    },
    backup: {
      title: '备份并清理',
      message: '将先执行一次全库备份（预计1~5分钟），再自动删除过期备份文件，是否继续？',
    },
    logs: {
      title: '清空系统日志',
      message: '仅清空 backend-web 服务的日志文件；websocket/scheduler 日志按100MB轮转+保留天数自动清理，是否继续？',
    },
  }

  const handleSingleCleanup = async (actionKey: string) => {
    if (busyAction) return
    const confirmConfig = SINGLE_CLEANUP_CONFIRM[actionKey]
    if (!confirmConfig) return
    if (!(await requestConfirm({
      title: confirmConfig.title,
      message: confirmConfig.message,
      confirmText: '清理',
      type: 'warning',
    }))) return

    setBusyAction(actionKey)
    try {
      if (actionKey === 'logs') {
        const res = await clearSystemLogs()
        if (!res.success) throw new Error(res.message || '清空日志失败')
        addToast({ type: 'success', message: '系统日志已清空' })
      } else {
        const taskCode = SINGLE_CLEANUP_TASKS[actionKey]
        const result = await triggerScheduledTask(taskCode)
        if (!result.success) throw new Error(result.message || '触发失败')
        let message = '清理已完成'
        if (actionKey === 'db') {
          try {
            const report = await getCleanupReport()
            message = `清理已完成，本轮共删除 ${report.total_deleted.toLocaleString()} 行`
          } catch {
            // 报告拉取失败不影响主流程
          }
        } else if (actionKey === 'backup') {
          message = '备份并清理已完成'
        }
        addToast({ type: 'success', message })
      }
      // 清理后失效目录体积缓存并刷新存储数据
      try {
        await refreshDirs()
      } catch {
        // 刷新失败不影响主流程
      }
      loadAll()
    } catch (e: any) {
      addToast({ type: 'error', message: e.message || '清理失败，请检查调度服务是否在线' })
    } finally {
      setBusyAction(null)
    }
  }

  const handleAckAlert = async (id: number) => {
    try {
      const result = await ackSystemAlert(id)
      if (result.success) {
        addToast({ type: 'success', message: '告警已确认' })
        loadAlerts()
      } else {
        addToast({ type: 'error', message: result.message || '确认失败' })
      }
    } catch {
      addToast({ type: 'error', message: '确认告警失败' })
    }
  }

  const host: HostMetrics | null = summary?.host ?? null
  const maxDisk = useMemo(() => {
    const disks = host?.disk ?? []
    if (!disks.length) return null
    return disks.reduce((a, b) => (a.percent > b.percent ? a : b))
  }, [host])
  const dbSize = tables?.db_total_size ?? 0

  if (!_hasHydrated) return <PageLoading />

  const trendData = useMemo(() => {
    return points.map((p) => ({
      ...p,
      tsLabel: p.ts.slice(5, 16).replace('T', ' '),
    }))
  }, [points])

  const pieData = useMemo(() => {
    const dirs = storage?.dirs ?? {}
    return Object.entries(dirs)
      .map(([name, info]) => ({ name, value: info.size_bytes }))
      .sort((a, b) => b.value - a.value)
  }, [storage])

  // 存储分布表格行（首行为数据库合成行，其余来自目录体积统计）
  const storageRows = useMemo(() => {
    const rows: Array<{
      name: string
      size: number | null
      files: number | null
      action?: string
      actionLabel?: string
      actionTitle?: string
    }> = [
      {
        name: '数据库（MySQL）',
        size: dbSize || null,
        files: null,
        action: 'db',
        actionLabel: '清理',
        actionTitle: '清理超期日志数据（保留策略默认30天）',
      },
    ]
    const dirActions: Record<string, { action: string; label: string; title: string }> = {
      '上传文件': { action: 'files', label: '清理', title: '清理孤儿/超期图片' },
      '浏览器数据': { action: 'browser', label: '清理', title: '仅清理禁用超10天账号的数据（启用账号登录态保留）' },
      '数据库备份': { action: 'backup', label: '备份并清理', title: '先执行一次全库备份再删过期备份（耗时较长）' },
      // 注意：「日志」行（根日志目录，launcher 日志/轨迹统计）无清理按钮——
      // launcher 日志自动轮转、轨迹统计自限，且清空 backend-web 日志与行内容不符
      '服务日志(backend-web)': { action: 'logs', label: '清理', title: '仅清空 backend-web 日志文件（其它服务日志按轮转自动清理）' },
    }
    for (const [name, info] of Object.entries(storage?.dirs ?? {})) {
      const mapping = dirActions[name]
      rows.push({
        name,
        size: info.size_bytes,
        files: info.file_count,
        ...(mapping ? { action: mapping.action, actionLabel: mapping.label, actionTitle: mapping.title } : {}),
      })
    }
    return rows
  }, [storage, dbSize])

  return (
    <div className="space-y-4">
      {/* 顶部工具栏 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-blue-500" />
          <h1 className="text-lg font-semibold text-slate-900 dark:text-slate-100">系统信息</h1>
          <span className="text-xs text-slate-400">
            {host?.hostname ?? '-'} · {host?.platform ?? '-'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={refreshInterval}
            onChange={(e) => setRefreshInterval(Number(e.target.value))}
            className="input-ios w-28 text-xs"
          >
            <option value={10}>每 10 秒刷新</option>
            <option value={30}>每 30 秒刷新</option>
            <option value={60}>每 60 秒刷新</option>
            <option value={0}>暂停自动刷新</option>
          </select>
          <button
            onClick={handleManualCollect}
            disabled={collecting}
            className="btn-ios-secondary inline-flex items-center gap-1.5"
          >
            <RefreshCw className={`w-4 h-4 ${collecting ? 'animate-spin' : ''}`} />
            {collecting ? '触发中...' : '手动采集'}
          </button>
          <button
            onClick={() => setCleanupOpen(true)}
            className="btn-ios-primary inline-flex items-center gap-1.5"
          >
            <Sparkles className="w-4 h-4" />
            一键清理
          </button>
        </div>
      </div>

      {loading ? (
        <PageLoading />
      ) : (
        <>
          {/* 顶部状态卡组 */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="vben-card !p-4 flex items-center justify-between">
              <div>
                <p className="text-xs text-slate-500 dark:text-slate-400 flex items-center gap-1">
                  <Cpu className="w-3.5 h-3.5" /> CPU
                </p>
                <div className="flex items-end gap-1 mt-2 mb-3">
                  <span className="text-2xl font-bold text-slate-900 dark:text-slate-100">
                    {host?.cpu_percent === null || host?.cpu_percent === undefined ? '-' : `${host.cpu_percent.toFixed(0)}%`}
                  </span>
                  <span className="text-xs text-slate-400">{host?.cpu_count ?? 0} 核</span>
                </div>
                {/* 每核微条形 */}
                {(host?.cpu_per_core?.length ?? 0) > 0 && (
                  <div className="flex items-end gap-0.5 h-6">
                    {host!.cpu_per_core!.slice(0, 24).map((v, i) => (
                      <div
                        key={i}
                        className="w-1.5 rounded-t"
                        style={{
                          height: `${Math.max(8, Math.min(100, v)) * 0.24}px`,
                          backgroundColor: v > 90 ? '#ef4444' : v > 70 ? '#f59e0b' : '#3b82f6',
                        }}
                        title={`核心${i}: ${v}%`}
                      />
                    ))}
                  </div>
                )}
              </div>
              <RingGauge percent={host?.cpu_percent ?? null} label="CPU" color="#3b82f6" />
            </div>

            <div className="vben-card !p-4 flex items-center justify-between">
              <div>
                <p className="text-xs text-slate-500 dark:text-slate-400 flex items-center gap-1">
                  <MemoryStick className="w-3.5 h-3.5" /> 内存
                </p>
                <div className="mt-2 mb-1">
                  <span className="text-2xl font-bold text-slate-900 dark:text-slate-100">
                    {host?.mem_percent === null || host?.mem_percent === undefined ? '-' : `${host.mem_percent.toFixed(1)}%`}
                  </span>
                </div>
                <p className="text-xs text-slate-500">{formatBytes(host?.mem_used)} / {formatBytes(host?.mem_total)}</p>
                <p className="text-xs text-slate-400">本进程 {formatBytes(host?.process_rss)}</p>
              </div>
              <RingGauge percent={host?.mem_percent ?? null} label="内存" color="#10b981" />
            </div>

            <div className="vben-card !p-4 flex items-center justify-between">
              <div>
                <p className="text-xs text-slate-500 dark:text-slate-400 flex items-center gap-1">
                  <HardDrive className="w-3.5 h-3.5" /> 磁盘
                </p>
                <div className="mt-2 mb-1">
                  <span className="text-2xl font-bold text-slate-900 dark:text-slate-100">
                    {maxDisk ? `${maxDisk.percent.toFixed(1)}%` : '-'}
                  </span>
                </div>
                <p className="text-xs text-slate-500">{maxDisk ? `${maxDisk.mountpoint} 剩余 ${formatBytes(maxDisk.free)}` : '无磁盘信息'}</p>
                {(host?.disk?.length ?? 0) > 1 && (
                  <p className="text-xs text-slate-400">共 {host!.disk.length} 个挂载点</p>
                )}
              </div>
              <RingGauge
                percent={maxDisk?.percent ?? null}
                label={maxDisk ? maxDisk.mountpoint : '磁盘'}
                color={maxDisk && maxDisk.percent >= 85 ? '#ef4444' : '#f59e0b'}
              />
            </div>

            <div className="vben-card !p-4">
              <p className="text-xs text-slate-500 dark:text-slate-400 flex items-center gap-1">
                <Server className="w-3.5 h-3.5" /> 系统
              </p>
              <div className="mt-2 space-y-1.5">
                <div className="flex justify-between text-xs">
                  <span className="text-slate-500">运行时长</span>
                  <span className="text-slate-700 dark:text-slate-300">{formatDuration(host?.uptime_seconds)}</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-slate-500">进程数</span>
                  <span className="text-slate-700 dark:text-slate-300">{host?.process_count ?? '-'}</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-slate-500">数据库体积</span>
                  <span className="text-slate-700 dark:text-slate-300">{formatBytes(dbSize)}</span>
                </div>
                <div className="flex justify-between text-xs items-center">
                  <span className="text-slate-500">网络</span>
                  <span className="text-slate-700 dark:text-slate-300 flex items-center gap-1">
                    <Wifi className="w-3 h-3 text-emerald-500" />
                    ↑{formatRate(host?.net?.sent_rate ?? 0)} ↓{formatRate(host?.net?.recv_rate ?? 0)}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* 服务状态 + 告警 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="vben-card">
              <div className="vben-card-header">
                <h2 className="vben-card-title"><Server className="w-4 h-4" />服务状态</h2>
              </div>
              <div className="vben-card-body flex flex-wrap gap-x-6 gap-y-2">
                <ServiceLight name="backend-web" available={true} />
                <ServiceLight name="websocket" available={summary?.services?.websocket?.available} />
                <ServiceLight name="scheduler" available={summary?.services?.scheduler?.available} />
                <ServiceLight name="MySQL" available={summary?.services?.mysql?.available} />
                <ServiceLight name="Redis" available={summary?.services?.redis?.available} />
              </div>
            </div>
            <div className="vben-card">
              <div className="vben-card-header">
                <h2 className="vben-card-title">
                  <AlertTriangle className="w-4 h-4 text-red-500" />活跃告警
                  {summary && summary.active_alert_count > 0 && (
                    <span className="ml-2 px-1.5 py-0.5 rounded-full bg-red-100 text-red-600 text-xs">
                      {summary.active_alert_count}
                    </span>
                  )}
                </h2>
              </div>
              <div className="vben-card-body space-y-2">
                {summary?.active_alerts?.length ? (
                  summary.active_alerts.map((alert) => (
                    <div key={alert.id} className="flex items-center justify-between text-sm">
                      <span className="flex items-center gap-1.5">
                        <span className={`w-2 h-2 rounded-full ${alert.level === 'critical' ? 'bg-red-500' : 'bg-amber-500'}`} />
                        <span className="text-slate-700 dark:text-slate-300">{alert.title}</span>
                      </span>
                      <button
                        onClick={() => handleAckAlert(alert.id)}
                        className="text-xs text-blue-500 hover:underline"
                      >
                        确认
                      </button>
                    </div>
                  ))
                ) : (
                  <p className="text-sm text-emerald-600">当前无活跃告警，系统运行正常 ✓</p>
                )}
              </div>
            </div>
          </div>

          {/* 趋势图 */}
          <div className="vben-card">
            <div className="vben-card-header">
              <h2 className="vben-card-title"><Activity className="w-4 h-4" />资源使用趋势</h2>
              <div className="flex gap-1">
                {[1, 24, 168].map((h) => (
                  <button
                    key={h}
                    onClick={() => setRangeHours(h)}
                    className={`px-2 py-1 text-xs rounded ${rangeHours === h ? 'bg-blue-500 text-white' : 'text-slate-500 hover:bg-slate-100'}`}
                  >
                    {h === 1 ? '1小时' : h === 24 ? '24小时' : '7天'}
                  </button>
                ))}
              </div>
            </div>
            <div className="vben-card-body">
              {trendData.length === 0 ? (
                <p className="text-sm text-slate-400 text-center py-8">
                  暂无历史指标数据（采集任务每分钟执行一次，稍后自动出现）
                </p>
              ) : (
                <div className="space-y-4">
                  <ResponsiveContainer width="100%" height={220}>
                    <AreaChart data={trendData}>
                      <defs>
                        <linearGradient id="gradCpu" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.35} />
                          <stop offset="100%" stopColor="#3b82f6" stopOpacity={0.05} />
                        </linearGradient>
                        <linearGradient id="gradMem" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#10b981" stopOpacity={0.35} />
                          <stop offset="100%" stopColor="#10b981" stopOpacity={0.05} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#94a3b833" />
                      <XAxis dataKey="tsLabel" tick={{ fontSize: 10 }} minTickGap={40} />
                      <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} unit="%" />
                      <Tooltip />
                      <Legend />
                      <Area type="monotone" dataKey="cpu" name="CPU %" stroke="#3b82f6" fill="url(#gradCpu)" isAnimationActive={false} />
                      <Area type="monotone" dataKey="mem" name="内存 %" stroke="#10b981" fill="url(#gradMem)" isAnimationActive={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                  <ResponsiveContainer width="100%" height={180}>
                    <LineChart data={trendData}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#94a3b833" />
                      <XAxis dataKey="tsLabel" tick={{ fontSize: 10 }} minTickGap={40} />
                      <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} unit="%" />
                      <Tooltip />
                      <Legend />
                      <Line type="monotone" dataKey="disk_max_percent" name="磁盘峰值 %" stroke="#f59e0b" dot={false} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          </div>

          {/* 存储分布 + 备份 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="vben-card">
              <div className="vben-card-header">
                <h2 className="vben-card-title"><HardDrive className="w-4 h-4" />存储分布</h2>
              </div>
              <div className="vben-card-body">
                <div className="flex items-center">
                  <ResponsiveContainer width="55%" height={200}>
                    <PieChart>
                      <Pie data={pieData} dataKey="value" nameKey="name" innerRadius={45} outerRadius={75} paddingAngle={2}>
                        {pieData.map((_, i) => (
                          <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip formatter={(value) => formatBytes(Number(value ?? 0))} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="flex-1 space-y-1.5">
                    {pieData.map((item, i) => (
                      <div key={item.name} className="flex items-center justify-between text-xs">
                        <span className="flex items-center gap-1.5">
                          <span className="w-2 h-2 rounded-full" style={{ backgroundColor: PIE_COLORS[i % PIE_COLORS.length] }} />
                          <span className="text-slate-600 dark:text-slate-300">{item.name}</span>
                        </span>
                        <span className="text-slate-500">{formatBytes(item.value)}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <table className="mt-3 w-full text-xs">
                  <thead>
                    <tr className="text-left text-slate-400 border-b border-slate-100 dark:border-slate-700">
                      <th className="py-1.5">分类</th>
                      <th>大小</th>
                      <th>文件数</th>
                      <th className="text-right">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {storageRows.map((row) => (
                      <tr key={row.name} className="border-b border-slate-50 dark:border-slate-800">
                        <td className="py-1.5 text-slate-700 dark:text-slate-300">{row.name}</td>
                        <td>{row.size === null ? '-' : formatBytes(row.size)}</td>
                        <td>{row.files === null ? '-' : row.files.toLocaleString()}</td>
                        <td className="text-right">
                          {row.action ? (
                            <button
                              onClick={() => handleSingleCleanup(row.action!)}
                              disabled={busyAction !== null}
                              title={row.actionTitle}
                              className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded border border-slate-200 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-slate-50 disabled:opacity-40"
                            >
                              {busyAction === row.action ? (
                                <Loader2 className="w-3 h-3 animate-spin" />
                              ) : (
                                <Trash2 className="w-3 h-3" />
                              )}
                              {busyAction === row.action ? '执行中...' : row.actionLabel}
                            </button>
                          ) : (
                            <span className="text-slate-300">-</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {/* 上次清理摘要 */}
                <p className="mt-3 text-xs text-slate-400">
                  {cleanupReport?.batch_time ? (
                    <>
                      上次清理：{cleanupReport.batch_time.slice(0, 16).replace('T', ' ')}，
                      共删除 {cleanupReport.total_deleted.toLocaleString()} 行（数据库）
                      {cleanupReport.last_backup
                        ? ` · 最近备份：${cleanupReport.last_backup.status === 'success' ? '成功' : '失败'}`
                        : ''}
                    </>
                  ) : (
                    '尚无清理记录（统一保留引擎每小时自动清理一次）'
                  )}
                </p>
              </div>
            </div>

            <div className="vben-card">
              <div className="vben-card-header">
                <h2 className="vben-card-title"><Database className="w-4 h-4" />数据库备份</h2>
              </div>
              <div className="vben-card-body max-h-[380px] overflow-y-auto">
                {storage?.backups?.length ? (
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-slate-400 border-b border-slate-100 dark:border-slate-700">
                        <th className="py-1.5">文件名</th>
                        <th>大小</th>
                        <th>时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {storage.backups.map((b) => (
                        <tr key={b.name} className="border-b border-slate-50 dark:border-slate-800">
                          <td className="py-1.5 text-slate-700 dark:text-slate-300">{b.name}</td>
                          <td>{formatBytes(b.size_bytes)}</td>
                          <td>{new Date(b.mtime * 1000).toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="text-sm text-slate-400 text-center py-8">暂无备份文件</p>
                )}
              </div>
            </div>
          </div>

          {/* 数据库详情 + 保留策略 */}
          <div className="vben-card">
            <div className="vben-card-header">
              <h2 className="vben-card-title"><Database className="w-4 h-4" />数据库详情（总 {formatBytes(dbSize)}）</h2>
            </div>
            <div className="vben-card-body space-y-4">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-slate-400 border-b border-slate-100 dark:border-slate-700">
                    <th className="py-1.5">表名（TOP 10）</th>
                    <th>行数</th>
                    <th>数据</th>
                    <th>索引</th>
                    <th>合计</th>
                  </tr>
                </thead>
                <tbody>
                  {(tables?.top_tables ?? []).map((t) => (
                    <tr key={t.table_name} className="border-b border-slate-50 dark:border-slate-800">
                      <td className="py-1.5 text-slate-700 dark:text-slate-300">{t.table_name}</td>
                      <td>{t.rows.toLocaleString()}</td>
                      <td>{formatBytes(t.data_length)}</td>
                      <td>{formatBytes(t.index_length)}</td>
                      <td>{formatBytes(t.data_length + t.index_length)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <div>
                <p className="text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
                  数据保留策略生效状态（统一数据保留引擎）
                </p>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-slate-400 border-b border-slate-100 dark:border-slate-700">
                      <th className="py-1.5">表名</th>
                      <th>保留天数</th>
                      <th>当前行数</th>
                      <th>最旧记录</th>
                      <th>最新记录</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(tables?.retention_policies ?? []).map((p) => (
                      <tr key={p.table_name} className="border-b border-slate-50 dark:border-slate-800">
                        <td className="py-1.5 text-slate-700 dark:text-slate-300">{p.table_name}</td>
                        <td>{p.retention_days} 天</td>
                        <td>{p.rows === null ? '-' : p.rows.toLocaleString()}</td>
                        <td className="text-slate-500">{p.oldest ? p.oldest.slice(0, 16).replace('T', ' ') : '-'}</td>
                        <td className="text-slate-500">{p.newest ? p.newest.slice(0, 16).replace('T', ' ') : '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* 告警列表 */}
          <div className="vben-card">
            <div className="vben-card-header">
              <h2 className="vben-card-title"><AlertTriangle className="w-4 h-4" />告警记录</h2>
              <div className="flex gap-1">
                {(['active', 'resolved', 'acked'] as const).map((s) => (
                  <button
                    key={s}
                    onClick={() => setAlertFilter(s)}
                    className={`px-2 py-1 text-xs rounded ${alertFilter === s ? 'bg-blue-500 text-white' : 'text-slate-500 hover:bg-slate-100'}`}
                  >
                    {s === 'active' ? '未恢复' : s === 'resolved' ? '已恢复' : '已确认'}
                  </button>
                ))}
              </div>
            </div>
            <div className="vben-card-body">
              {alerts.length === 0 ? (
                <p className="text-sm text-slate-400 text-center py-4">暂无记录</p>
              ) : (
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-slate-400 border-b border-slate-100 dark:border-slate-700">
                      <th className="py-1.5">时间</th>
                      <th>类型</th>
                      <th>级别</th>
                      <th>标题</th>
                      <th>来源</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {alerts.map((a) => (
                      <tr key={a.id} className="border-b border-slate-50 dark:border-slate-800">
                        <td className="py-1.5 text-slate-500">{a.created_at ? a.created_at.slice(0, 16).replace('T', ' ') : '-'}</td>
                        <td>{a.alert_type}</td>
                        <td>
                          <span className={`px-1.5 py-0.5 rounded text-xs ${a.level === 'critical' ? 'bg-red-100 text-red-600' : 'bg-amber-100 text-amber-600'}`}>
                            {a.level === 'critical' ? '严重' : '警告'}
                          </span>
                        </td>
                        <td className="text-slate-700 dark:text-slate-300">{a.title}</td>
                        <td className="text-slate-500">{a.source}</td>
                        <td>
                          {a.status === 'active' && (
                            <button onClick={() => handleAckAlert(a.id)} className="text-blue-500 hover:underline">确认</button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </>
      )}

      {/* 一键清理弹窗（三态：确认 → 执行 → 结果） */}
      <CleanupModal
        isOpen={cleanupOpen}
        onClose={() => setCleanupOpen(false)}
        onFinished={() => loadAll()}
      />

      {/* 单类清理确认弹窗 */}
      <ConfirmModal
        isOpen={confirmDialog !== null}
        title={confirmDialog?.title ?? '确认操作'}
        message={confirmDialog?.message ?? ''}
        confirmText={confirmDialog?.confirmText ?? '确定'}
        type={confirmDialog?.type ?? 'warning'}
        onConfirm={() => closeConfirm(true)}
        onCancel={() => closeConfirm(false)}
      />
    </div>
  )
}
