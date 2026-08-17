/**
 * 定时发布管理页面
 *
 * 功能：
 * 1. Tab 1 - 定时规则列表：查看/新建/编辑/删除/开关/手动触发
 * 2. Tab 2 - 执行历史：查看所有规则的历史执行记录
 */
import { useState, useEffect, useCallback, useRef, Fragment } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Clock, History, Plus, Pencil, Trash2, Play, Power, PowerOff, RefreshCw, ChevronLeft, ChevronRight, ChevronDown, ChevronUp, Loader2, Layers, CheckCircle, XCircle } from 'lucide-react'
import { useUIStore } from '@/store/uiStore'
import { getSchedules, deleteSchedule, toggleSchedule, triggerSchedule, clearScheduleLogs, getScheduleHistory, batchDeleteScheduleLogs, getActiveScheduleProgress, type PublishSchedule, type ScheduleHistoryItem, type ScheduleLogDetail, type ActiveScheduleProgress } from '@/api/productPublish'
import { clearOfflineScheduleLogs, getOfflineSchedules, type OfflineLogDetail } from '@/api/offlineSchedules'
import { PageLoading } from '@/components/common/Loading'
import { ConfirmModal } from '@/components/common/ConfirmModal'
import { ScheduleFormModal } from './ScheduleFormModal'
import { OfflineRules } from './OfflineRules'
import { OfflineLogDetailPanel } from './OfflineHistory'

type Tab = 'publish-rules' | 'offline-rules' | 'schedule-history'

const MODE_LABELS: Record<string, string> = { once: '单次', daily: '每天', weekly: '每周' }
const STATUS_CONFIG: Record<string, { label: string; cls: string }> = {
  pending:   { label: '待执行', cls: 'badge-gray' },
  running:   { label: '执行中', cls: 'badge-warning' },
  completed: { label: '已完成', cls: 'badge-success' },
  failed:    { label: '失败',   cls: 'badge-danger' },
  cancelled: { label: '已取消', cls: 'badge-gray' },
}

const getSyncStatusLabel = (status: string) => {
  if (status === 'success') return '已成功'
  if (status === 'failed') return '失败'
  if (status === 'running') return '获取中'
  if (status === 'skipped') return '未触发'
  if (status === 'unknown') return '状态未知'
  return '待执行'
}

const getSyncStatusClassName = (status: string) => {
  if (status === 'success') return 'badge-success'
  if (status === 'failed') return 'badge-danger'
  if (status === 'running') return 'badge-info'
  if (status === 'skipped') return 'badge-warning'
  if (status === 'unknown') return 'badge-warning'
  return 'badge-secondary'
}

const MATERIAL_RESULT_CONFIG: Record<string, { label: string; cls: string }> = {
  success: { label: '成功', cls: 'badge-success' },
  failed: { label: '失败', cls: 'badge-danger' },
  account_error: { label: '账号错误', cls: 'badge-warning' },
}

/** 执行记录明细面板（detail_json 展开） */
function LogDetailPanel({ detail }: { detail: ScheduleLogDetail }) {
  const rounds = detail.rounds || []
  const filtered = detail.filtered || []
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap gap-2 items-center">
        <span className="badge-gray">模式：{detail.publish_mode === 'random' ? '随机发布' : '指定发布'}</span>
        {detail.random_count != null && <span className="badge-gray">目标 {detail.random_count} 条</span>}
        {detail.deduplicate && <span className="badge-gray">已启用去重</span>}
        {detail.target_ok != null && <span className="badge-gray">素材成功 {detail.target_ok} 条</span>}
        {(detail.filtered_count != null ? detail.filtered_count : filtered.length) > 0 && (
          <span className="badge-gray">过滤 {detail.filtered_count ?? filtered.length} 条</span>
        )}
        {detail.detail_truncated && (
          <span className="badge-warning">素材过多，仅展示账号结果计数</span>
        )}
      </div>
      {rounds.map(r => (
        <div key={r.round}>
          <div className="font-medium text-slate-600 dark:text-slate-300 mb-1">第 {r.round} 轮（{r.materials.length} 条素材）</div>
          <div className="space-y-1">
            {r.materials.map((m, i) => {
              const cfg = MATERIAL_RESULT_CONFIG[m.result] || { label: m.result, cls: 'badge-gray' }
              const accounts = m.accounts || []
              const failedAccounts = accounts.filter(a => a.status !== 'success')
              const counts = m.account_counts
              return (
                <div key={i} className="flex flex-wrap items-center gap-2">
                  <span className={cfg.cls}>{cfg.label}</span>
                  <span className="font-mono text-slate-500">{m.item_no ?? '无编号'}</span>
                  <span className="truncate max-w-[280px] text-slate-700 dark:text-slate-200" title={m.title}>{m.title}</span>
                  {failedAccounts.length > 0 && (
                    <span className="text-xs text-red-500" title={failedAccounts.map(a => a.error).join('；')}>
                      {failedAccounts.map(a => a.account_id).join('、')} 失败
                    </span>
                  )}
                  {counts && !accounts.length && (
                    <span className="text-xs text-slate-400">
                      成功 {counts.success} · 失败 {counts.failed} · 账号错误 {counts.account_error}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ))}
      {filtered.length > 0 && (
        <div>
          <div className="font-medium text-slate-600 dark:text-slate-300 mb-1">被去重过滤的素材（{filtered.length}）</div>
          <div className="flex flex-wrap gap-2">
            {filtered.map((f, i) => (
              <span key={i} className="badge-gray">
                {f.item_no ?? '无编号'}
              </span>
            ))}
          </div>
        </div>
      )}
      {rounds.length === 0 && filtered.length === 0 && (
        <p className="text-slate-400">暂无明细</p>
      )}
    </div>
  )
}

function formatNextTrigger(dt: string | null | undefined): string {
  if (!dt) return '-'
  const d = new Date(dt)
  const now = Date.now()
  const diff = d.getTime() - now
  if (diff < 0) return '已到期'
  if (diff < 60000) return '即将执行'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟后`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时后`
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function formatConfig(sc: PublishSchedule): string {
  const cfg = sc.schedule_config
  if (sc.schedule_mode === 'once') {
    if (cfg?.datetime) return new Date(cfg.datetime).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    return '未设置'
  }
  if (cfg?.random && cfg?.time_range) {
    return `${cfg.time_range.start}~${cfg.time_range.end} 随机`
  }
  if (cfg?.times?.length) {
    return cfg.times.join(', ')
  }
  return '-'
}

export function ScheduledPublish() {
  const { addToast } = useUIStore()
  const [tab, setTab] = useState<Tab>('publish-rules')
  const [loading, setLoading] = useState(true)
  const [offlineRulesTotal, setOfflineRulesTotal] = useState(0)

  // 规则列表
  const [schedules, setSchedules] = useState<PublishSchedule[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [totalPages, setTotalPages] = useState(0)
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [showForm, setShowForm] = useState(false)
  const [editTarget, setEditTarget] = useState<PublishSchedule | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<PublishSchedule | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [batchDeleteConfirm, setBatchDeleteConfirm] = useState(false)
  const [batchDeleting, setBatchDeleting] = useState(false)

  // 活跃进度（定时任务执行时的实时进度面板）
  const [activeProgressList, setActiveProgressList] = useState<ActiveScheduleProgress[]>([])
  const [expandedPanels, setExpandedPanels] = useState<Set<number>>(new Set())
  const progressPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 历史记录（发布与下架合并视图）
  const [historyItems, setHistoryItems] = useState<ScheduleHistoryItem[]>([])
  const [historyTotal, setHistoryTotal] = useState(0)
  const [historyPage, setHistoryPage] = useState(1)
  const [historyTotalPages, setHistoryTotalPages] = useState(0)
  const [showClearLogsConfirm, setShowClearLogsConfirm] = useState(false)
  const [clearingLogs, setClearingLogs] = useState(false)
  const [clearDays, setClearDays] = useState(10) // 保留天数，0=清空全部，默认10天
  const [expandedLogKeys, setExpandedLogKeys] = useState<Set<string>>(new Set())
  const [selectedLogKeys, setSelectedLogKeys] = useState<string[]>([])
  const [batchDeleteLogsConfirm, setBatchDeleteLogsConfirm] = useState(false)
  const [batchDeletingLogs, setBatchDeletingLogs] = useState(false)

  /** 展开/收起执行记录明细（key = 类别-记录ID，两张表ID可能重复） */
  const toggleLogExpand = (key: string) => {
    setExpandedLogKeys(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  /** 全选/取消全选当前页执行记录 */
  const handleSelectAllLogs = () => {
    if (historyItems.length === 0) return
    const keys = historyItems.map(l => `${l.rule_type}-${l.log_id}`)
    const allSelected = keys.every(k => selectedLogKeys.includes(k))
    if (allSelected) {
      setSelectedLogKeys(prev => prev.filter(k => !keys.includes(k)))
    } else {
      setSelectedLogKeys(prev => [...new Set([...prev, ...keys])])
    }
  }

  const toggleSelectLog = (key: string) => {
    setSelectedLogKeys(prev => prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key])
  }

  /** 批量删除选中的执行记录（跨发布/下架两张表） */
  const handleBatchDeleteLogs = async () => {
    if (selectedLogKeys.length === 0) return
    setBatchDeletingLogs(true)
    try {
      const items = selectedLogKeys.map(k => {
        const idx = k.indexOf('-')
        return { rule_type: k.slice(0, idx), log_id: Number(k.slice(idx + 1)) }
      })
      const res = await batchDeleteScheduleLogs(items)
      if (res.success) {
        addToast({ type: 'success', message: res.message || '删除成功' })
        setBatchDeleteLogsConfirm(false)
        setSelectedLogKeys([])
        if (historyPage === 1) await loadHistory(1)
        else setHistoryPage(1)
      } else {
        addToast({ type: 'error', message: res.message || '删除失败' })
      }
    } catch {
      addToast({ type: 'error', message: '删除失败' })
    } finally {
      setBatchDeletingLogs(false)
    }
  }

  const loadSchedules = useCallback(async (p = page, size = pageSize) => {
    try {
      const res = await getSchedules(p, size)
      if (res.success) {
        setSchedules(res.data.list)
        setTotal(res.data.total)
        setTotalPages(res.data.total_pages)
        const currentIds = new Set(res.data.list.map((s: PublishSchedule) => s.id))
        setSelectedIds(prev => prev.filter(id => currentIds.has(id)))
      }
    } catch { /* ignore */ }
  }, [page, pageSize])

  const loadHistory = useCallback(async (p = historyPage) => {
    try {
      const res = await getScheduleHistory(p, 20)
      if (res.success) {
        setHistoryItems(res.data.list)
        setHistoryTotal(res.data.total)
        setHistoryTotalPages(res.data.total_pages)
        // 清理已不在当前页的选中项
        const currentKeys = new Set(res.data.list.map(l => `${l.rule_type}-${l.log_id}`))
        setSelectedLogKeys(prev => prev.filter(k => currentKeys.has(k)))
      }
    } catch { /* ignore */ }
  }, [historyPage])

  useEffect(() => {
    setLoading(true)
    loadSchedules().finally(() => setLoading(false))
  }, [loadSchedules])

  useEffect(() => {
    // 启动时拉取下架规则总数（Tab 角标需要；下架规则组件懒挂载，进入该 Tab 后由其继续同步）
    getOfflineSchedules(1, 20)
      .then(res => { if (res.success) setOfflineRulesTotal(res.data.total) })
      .catch(() => { /* ignore */ })
  }, [])

  useEffect(() => {
    if (tab === 'schedule-history') loadHistory()
  }, [tab, loadHistory])

  // 轮询活跃的定时发布任务进度
  useEffect(() => {
    const pollProgress = async () => {
      try {
        const res = await getActiveScheduleProgress()
        if (res.success && res.data?.tasks) {
          setActiveProgressList(res.data.tasks)
        }
      } catch { /* 静默处理 */ }
    }
    // 立即查询一次
    pollProgress()
    // 每3秒轮询
    progressPollRef.current = setInterval(pollProgress, 3000)
    return () => {
      if (progressPollRef.current) clearInterval(progressPollRef.current)
    }
  }, [])

  /** 切换进度面板折叠 */
  const togglePanel = (scheduleLogId: number) => {
    setExpandedPanels(prev => {
      const next = new Set(prev)
      if (next.has(scheduleLogId)) {
        next.delete(scheduleLogId)
      } else {
        next.add(scheduleLogId)
      }
      return next
    })
  }

  /** 全选/取消全选当前页 */
  const handleSelectAll = () => {
    if (schedules.length === 0) return
    const currentPageIds = schedules.map(s => s.id)
    const allSelected = currentPageIds.every(id => selectedIds.includes(id))
    if (allSelected) {
      setSelectedIds(prev => prev.filter(id => !currentPageIds.includes(id)))
    } else {
      setSelectedIds(prev => [...new Set([...prev, ...currentPageIds])])
    }
  }

  const toggleSelect = (id: number) => {
    setSelectedIds(prev => prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id])
  }

  const handlePageSizeChange = (size: number) => {
    setPageSize(size)
    setPage(1)
  }

  const allCurrentSelected = schedules.length > 0 && schedules.every(s => selectedIds.includes(s.id))

  const handleToggle = async (s: PublishSchedule) => {
    try {
      const res = await toggleSchedule(s.id)
      if (res.success) {
        addToast({ type: 'success', message: res.message || '操作成功' })
        loadSchedules(page, pageSize)
      } else {
        addToast({ type: 'error', message: res.message || '操作失败' })
      }
    } catch { addToast({ type: 'error', message: '操作失败' }) }
  }

  const handleTrigger = async (s: PublishSchedule) => {
    try {
      const res = await triggerSchedule(s.id)
      if (res.success) {
        addToast({ type: 'success', message: '已手动触发，请查看执行历史' })
        loadSchedules(page, pageSize)
        // 乐观更新：立即在进度面板中显示
        const data = res.data as Record<string, unknown> | undefined
        const batchId = data?.batch_id as string | undefined
        const logId = data?.log_id as number | undefined
        if (batchId && logId) {
          const total = s.account_ids.length * s.material_ids.length
          const optimisticEntry: ActiveScheduleProgress = {
            schedule_log_id: logId,
            schedule_id: s.id,
            schedule_name: s.name,
            batch_id: batchId,
            scheduled_at: new Date().toISOString(),
            progress: {
              total,
              success: 0,
              failed: 0,
              publishing: 0,
              pending: total,
              finished: false,
              account_statuses: s.account_ids.map(aid => ({
                account_id: aid,
                total: s.material_ids.length,
                success: 0,
                failed: 0,
                publishing: 0,
                pending: s.material_ids.length,
                sync_status: 'pending' as const,
                sync_message: '等待该账号发布完成后自动获取商品',
                sync_total_count: 0,
                sync_saved_count: 0,
              })),
            },
          }
          setActiveProgressList(prev => [optimisticEntry, ...prev.filter(p => p.schedule_log_id !== logId)])
        }
      } else {
        addToast({ type: 'error', message: res.message || '触发失败' })
      }
    } catch { addToast({ type: 'error', message: '触发失败' }) }
  }

  const handleDelete = async () => {
    if (!deleteConfirm) return
    setDeleting(true)
    try {
      const res = await deleteSchedule(deleteConfirm.id)
      if (res.success) {
        addToast({ type: 'success', message: '规则已删除' })
        setDeleteConfirm(null)
        loadSchedules(page, pageSize)
      } else {
        addToast({ type: 'error', message: res.message || '删除失败' })
      }
    } catch { addToast({ type: 'error', message: '删除失败' }) }
    finally { setDeleting(false) }
  }

  /** 批量删除 */
  const handleBatchDelete = async () => {
    if (selectedIds.length === 0) return
    setBatchDeleting(true)
    let successCount = 0
    let failCount = 0
    for (const id of selectedIds) {
      try {
        const res = await deleteSchedule(id)
        if (res.success) successCount++
        else failCount++
      } catch { failCount++ }
    }
    if (successCount > 0) {
      addToast({ type: 'success', message: `成功删除 ${successCount} 条规则${failCount > 0 ? `，${failCount} 条失败` : ''}` })
    } else {
      addToast({ type: 'error', message: '删除失败' })
    }
    setBatchDeleteConfirm(false)
    setSelectedIds([])
    loadSchedules(page, pageSize)
    setBatchDeleting(false)
  }

  const handleClearLogs = async () => {
    setClearingLogs(true)
    try {
      const days = clearDays > 0 ? clearDays : undefined
      // 同时清空发布与下架执行日志（定时历史合并视图）
      const [pubRes, offRes] = await Promise.all([
        clearScheduleLogs(days),
        clearOfflineScheduleLogs(days),
      ])
      if (pubRes.success || offRes.success) {
        addToast({ type: 'success', message: '定时历史已清空' })
        setShowClearLogsConfirm(false)
        if (historyPage === 1) await loadHistory(1)
        else setHistoryPage(1)
      } else {
        addToast({ type: 'error', message: '清空失败' })
      }
    } catch {
      addToast({ type: 'error', message: '清空失败' })
    } finally {
      setClearingLogs(false)
    }
  }

  if (loading) return <PageLoading />

  return (
    <div className="space-y-3 sm:space-y-4">
      {/* 标题栏 */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="page-title">定时管理</h1>
          <p className="page-description">设置定时规则，到时间自动执行批量发布与自动下架</p>
        </div>
      </div>

      {/* Tab 切换 */}
      <div className="flex gap-1 bg-slate-100 dark:bg-slate-800 p-1 rounded-lg w-fit">
        {([
          { key: 'publish-rules', label: '发布规则', icon: Clock },
          { key: 'offline-rules', label: '下架规则', icon: Clock },
          { key: 'schedule-history', label: '定时历史', icon: History },
        ] as { key: Tab; label: string; icon: any }[]).map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              tab === t.key
                ? 'bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 shadow-sm'
                : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
            }`}>
            <t.icon className="w-4 h-4" />
            {t.label}
            {t.key === 'publish-rules' && total > 0 && (
              <span className="text-xs bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-400 px-1.5 py-0.5 rounded-full">{total}</span>
            )}
            {t.key === 'offline-rules' && offlineRulesTotal > 0 && (
              <span className="text-xs bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-400 px-1.5 py-0.5 rounded-full">{offlineRulesTotal}</span>
            )}
          </button>
        ))}
      </div>

      {/* Tab 1: 定时规则列表 */}
      {tab === 'publish-rules' && (
        <div className="space-y-3">
          {/* 标题栏（与下架规则布局统一） */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div>
              <p className="page-description">按时间配置自动触发批量发布</p>
            </div>
            <div className="flex gap-2">
              {selectedIds.length > 0 && (
                <button className="btn-ios-danger" onClick={() => setBatchDeleteConfirm(true)}>
                  <Trash2 className="w-4 h-4" />批量删除 ({selectedIds.length})
                </button>
              )}
              <button className="btn-ios-secondary" onClick={() => loadSchedules(page, pageSize)}>
                <RefreshCw className="w-4 h-4" />刷新
              </button>
              <button className="btn-ios-primary" onClick={() => { setEditTarget(null); setShowForm(true) }}>
                <Plus className="w-4 h-4" />新建规则
              </button>
            </div>
          </div>

          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
            className="vben-card flex flex-col" style={{ height: 'calc(100vh - 260px)', minHeight: '400px' }}>
            <div className="vben-card-header">
              <h2 className="vben-card-title"><Clock className="w-4 h-4" />定时规则</h2>
              <span className="badge-primary">共 {total} 条</span>
            </div>
          <div className="flex-1 overflow-x-auto overflow-y-auto">
            <table className="table-ios">
              <thead className="sticky top-0 bg-white dark:bg-slate-800 z-10">
                <tr>
                  <th className="w-10">
                    <input type="checkbox" checked={allCurrentSelected && schedules.length > 0}
                      onChange={handleSelectAll}
                      className="w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" />
                  </th>
                  <th>规则名称</th>
                  <th>模式</th>
                  <th>时间配置</th>
                  <th>账号/素材</th>
                  <th>下次触发</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {schedules.length === 0 ? (
                  <tr><td colSpan={8} className="text-center py-12 text-slate-400">
                    <div className="flex flex-col items-center gap-2"><Clock className="w-12 h-12 text-slate-300" />
                      <p>暂无定时规则，点击「新建规则」开始</p></div>
                  </td></tr>
                ) : schedules.map(s => (
                  <tr key={s.id} className={selectedIds.includes(s.id) ? 'bg-blue-50 dark:bg-blue-900/10' : ''}>
                    <td>
                      <input type="checkbox" checked={selectedIds.includes(s.id)}
                        onChange={() => toggleSelect(s.id)}
                        className="w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" />
                    </td>
                    <td>
                      <span className="font-medium text-slate-800 dark:text-slate-100 truncate block max-w-[180px]" title={s.name}>{s.name}</span>
                    </td>
                    <td>
                      <span className="badge-gray">{MODE_LABELS[s.schedule_mode] || s.schedule_mode}</span>
                      {s.publish_mode === 'random' && (
                        <div className="text-xs text-slate-400 mt-0.5">
                          随机 {s.random_count} 条{s.deduplicate_enabled ? ' · 去重' : ''}
                        </div>
                      )}
                    </td>
                    <td className="text-sm text-slate-500">{formatConfig(s)}</td>
                    <td className="text-sm text-slate-500">
                      {(s.account_count ?? s.account_ids.length)} 账号 / {(s.material_count ?? s.material_ids.length)} 素材
                    </td>
                    <td>
                      <span className={`text-sm ${!s.enabled ? 'text-slate-400' : s.next_trigger_at && new Date(s.next_trigger_at).getTime() < Date.now() ? 'text-amber-600 font-medium' : 'text-slate-500'}`}>
                        {s.enabled ? formatNextTrigger(s.next_trigger_at) : '-'}
                      </span>
                    </td>
                    <td>
                      <span className={s.enabled ? 'badge-success' : 'badge-gray'}>
                        {s.enabled ? <Power className="w-3 h-3 mr-1 inline" /> : <PowerOff className="w-3 h-3 mr-1 inline" />}
                        {s.enabled ? '启用' : '禁用'}
                      </span>
                    </td>
                    <td>
                      <div className="table-actions">
                        <button className="table-action-btn" title="立即执行" onClick={() => handleTrigger(s)}>
                          <Play className="w-4 h-4 text-green-500" />
                        </button>
                        <button className="table-action-btn" title="编辑" onClick={() => { setEditTarget(s); setShowForm(true) }}>
                          <Pencil className="w-4 h-4 text-blue-500" />
                        </button>
                        <button className="table-action-btn" title={s.enabled ? '禁用' : '启用'} onClick={() => handleToggle(s)}>
                          {s.enabled ? <PowerOff className="w-4 h-4 text-amber-500" /> : <Power className="w-4 h-4 text-green-500" />}
                        </button>
                        <button className="table-action-btn" title="删除" onClick={() => setDeleteConfirm(s)}>
                          <Trash2 className="w-4 h-4 text-red-500" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {total > 0 && (
            <div className="flex-shrink-0 flex flex-col sm:flex-row items-center justify-between px-4 py-3 border-t border-slate-200 dark:border-slate-700 gap-3">
              <div className="flex items-center gap-2 text-sm text-slate-500">
                <span>每页</span>
                <select value={pageSize} onChange={e => handlePageSizeChange(Number(e.target.value))}
                  className="px-2 py-1 border border-slate-300 dark:border-slate-600 rounded-md bg-white dark:bg-slate-800 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                  <option value={10}>10 条</option>
                  <option value={20}>20 条</option>
                  <option value={50}>50 条</option>
                  <option value={100}>100 条</option>
                </select>
                <span>共 {total} 条</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm text-slate-500">第 {page} / {totalPages} 页</span>
                <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1}
                  className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
                  <ChevronLeft className="w-4 h-4" /></button>
                <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages}
                  className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
                  <ChevronRight className="w-4 h-4" /></button>
              </div>
            </div>
          )}
          </motion.div>
        </div>
      )}

      {/* Tab 2: 定时历史（发布与下架合并视图） */}
      {tab === 'schedule-history' && (
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
          className="vben-card flex flex-col" style={{ height: 'calc(100vh - 260px)', minHeight: '400px' }}>
          <div className="vben-card-header">
            <h2 className="vben-card-title"><History className="w-4 h-4" />定时历史</h2>
            <div className="flex items-center gap-2">
              {selectedLogKeys.length > 0 && (
                <button
                  onClick={() => setBatchDeleteLogsConfirm(true)}
                  className="btn-ios-danger btn-sm"
                  disabled={batchDeletingLogs}
                >
                  <Trash2 className="w-3.5 h-3.5" />批量删除 ({selectedLogKeys.length})
                </button>
              )}
              <div className="flex items-center gap-1">
                <span className="text-sm text-slate-500">保留</span>
                <input
                  type="number"
                  min={0}
                  max={3650}
                  value={clearDays}
                  onChange={e => setClearDays(Math.max(0, parseInt(e.target.value) || 0))}
                  className="input-ios w-16 text-center"
                  title="0=清空全部，N=保留最近N天"
                />
                <span className="text-sm text-slate-500">天</span>
              </div>
              <button
                onClick={() => setShowClearLogsConfirm(true)}
                className="btn-ios-danger btn-sm"
                title={clearDays > 0 ? `清空${clearDays}天前的执行日志` : '清空全部执行日志'}
                disabled={clearingLogs}
              >
                <Trash2 className="w-3.5 h-3.5" />清空日志
              </button>
              <button className="btn-ios-secondary btn-sm" onClick={() => loadHistory(historyPage)}>
                <RefreshCw className="w-3.5 h-3.5" />刷新
              </button>
              <span className="badge-primary">共 {historyTotal} 条</span>
            </div>
          </div>
          <div className="flex-1 overflow-x-auto overflow-y-auto">
            <table className="table-ios">
              <thead className="sticky top-0 bg-white dark:bg-slate-800 z-10">
                <tr>
                  <th className="w-10">
                    <input type="checkbox"
                      checked={historyItems.length > 0 && historyItems.every(l => selectedLogKeys.includes(`${l.rule_type}-${l.log_id}`))}
                      onChange={handleSelectAllLogs}
                      className="w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" />
                  </th>
                  <th>类别</th>
                  <th>规则名</th>
                  <th>计划时间</th>
                  <th>执行时间</th>
                  <th>结果</th>
                  <th>状态</th>
                  <th>明细</th>
                </tr>
              </thead>
              <tbody>
                {historyItems.length === 0 ? (
                  <tr><td colSpan={8} className="text-center py-12 text-slate-400">
                    <div className="flex flex-col items-center gap-2"><History className="w-12 h-12 text-slate-300" />
                      <p>暂无执行记录</p></div>
                  </td></tr>
                ) : historyItems.map(l => {
                  const rowKey = `${l.rule_type}-${l.log_id}`
                  const isExpanded = expandedLogKeys.has(rowKey)
                  return (
                    <Fragment key={rowKey}>
                      <tr className={isExpanded || selectedLogKeys.includes(rowKey) ? 'bg-blue-50 dark:bg-blue-900/10' : ''}>
                        <td>
                          <input type="checkbox" checked={selectedLogKeys.includes(rowKey)}
                            onChange={() => toggleSelectLog(rowKey)}
                            className="w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" />
                        </td>
                        <td>
                          <span className={l.rule_type === 'offline' ? 'badge-warning' : 'badge-info'}>
                            {l.rule_type === 'offline' ? '下架' : '发布'}
                          </span>
                        </td>
                        <td className="text-sm whitespace-nowrap">
                          <span className="font-medium text-slate-800 dark:text-slate-100 truncate block max-w-[150px]" title={l.schedule_name || `规则 #${l.schedule_id}`}>
                            {l.schedule_name || `规则 #${l.schedule_id}`}
                          </span>
                        </td>
                        <td className="text-sm whitespace-nowrap">
                          {new Date(l.scheduled_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                        </td>
                        <td className="text-sm whitespace-nowrap text-slate-500">
                          {l.executed_at ? new Date(l.executed_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '-'}
                        </td>
                        <td className="text-sm">
                          <span className="text-green-600">{l.success_count} 成功</span>
                          {l.failed_count > 0 && <span className="text-red-500 ml-1">/ {l.failed_count} 失败</span>}
                          <span className="text-slate-400 ml-1">/ {l.total_count} {l.rule_type === 'offline' ? '候选' : '总'}</span>
                          {l.error_message && (
                            <div className="text-xs text-red-400 truncate max-w-[200px]" title={l.error_message}>{l.error_message}</div>
                          )}
                        </td>
                        <td>
                          <span className={STATUS_CONFIG[l.status]?.cls || 'badge-gray'}>
                            {STATUS_CONFIG[l.status]?.label || l.status}
                          </span>
                        </td>
                        <td>
                          <button
                            onClick={() => toggleLogExpand(rowKey)}
                            disabled={!l.detail_json}
                            className="p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed text-slate-500"
                            title={l.detail_json ? '展开明细' : '暂无明细'}
                          >
                            {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                          </button>
                        </td>
                      </tr>
                      {isExpanded && l.detail_json && (
                        <tr>
                          <td colSpan={8} className="bg-slate-50 dark:bg-slate-800/60 px-4 py-3 border-t border-slate-200 dark:border-slate-700">
                            {l.rule_type === 'offline'
                              ? <OfflineLogDetailPanel detail={l.detail_json as OfflineLogDetail} />
                              : <LogDetailPanel detail={l.detail_json as ScheduleLogDetail} />}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
          {historyTotalPages > 1 && (
            <div className="flex-shrink-0 flex items-center justify-between px-4 py-3 border-t border-slate-200 dark:border-slate-700">
              <span className="text-sm text-slate-500">{historyTotal} 条，第 {historyPage}/{historyTotalPages} 页</span>
              <div className="flex gap-1">
                <button onClick={() => setHistoryPage(p => Math.max(1, p - 1))} disabled={historyPage <= 1}
                  className="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-50">
                  <ChevronLeft className="w-4 h-4" /></button>
                <button onClick={() => setHistoryPage(p => Math.min(historyTotalPages, p + 1))} disabled={historyPage >= historyTotalPages}
                  className="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-50">
                  <ChevronRight className="w-4 h-4" /></button>
              </div>
            </div>
          )}
        </motion.div>
      )}

      {/* Tab 3: 下架规则 */}
      {tab === 'offline-rules' && <OfflineRules onTotalChange={setOfflineRulesTotal} />}

      {/* 定时发布实时进度面板 */}
      {activeProgressList.length > 0 && (
        <div className="space-y-3">
          {activeProgressList.map((task) => {
            const isExpanded = expandedPanels.has(task.schedule_log_id)
            const p = task.progress
            const isFinished = p?.finished ?? false
            return (
              <motion.div
                key={task.schedule_log_id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                className="vben-card"
              >
                {/* 折叠头 */}
                <button
                  onClick={() => togglePanel(task.schedule_log_id)}
                  className="vben-card-header w-full text-left cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors rounded-t-xl"
                >
                  <div className="flex items-center gap-3 flex-1 min-w-0">
                    <h2 className="vben-card-title flex items-center gap-2 min-w-0">
                      <Layers className="w-4 h-4 flex-shrink-0" />
                      <span className="truncate">{task.schedule_name}</span>
                    </h2>
                    {isFinished ? (
                      <span className="badge-success flex-shrink-0">已完成</span>
                    ) : (
                      <span className="badge-info flex-shrink-0 flex items-center gap-1">
                        <Loader2 className="w-3 h-3 animate-spin" />执行中
                      </span>
                    )}
                  </div>
                  <span className="text-slate-400 flex-shrink-0">
                    {isExpanded ? <ChevronUp className="w-5 h-5" /> : <ChevronDown className="w-5 h-5" />}
                  </span>
                </button>

                {/* 折叠体 */}
                {isExpanded && p && (
                  <div className="vben-card-body border-t border-slate-200 dark:border-slate-700 pt-4">
                    {/* 统计卡片 */}
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
                      {[
                        { label: '总数', value: p.total, icon: <Layers className="w-5 h-5" />, cls: 'stat-icon-primary' },
                        { label: '成功', value: p.success, icon: <CheckCircle className="w-5 h-5" />, cls: 'stat-icon-success' },
                        { label: '失败', value: p.failed, icon: <XCircle className="w-5 h-5" />, cls: 'stat-icon-warning' },
                        { label: '进行中', value: p.publishing + p.pending, icon: <Clock className="w-5 h-5" />, cls: 'stat-icon-info' },
                      ].map(item => (
                        <div key={item.label} className="stat-card">
                          <div className={item.cls}>{item.icon}</div>
                          <div>
                            <div className="stat-value">{item.value}</div>
                            <div className="stat-label">{item.label}</div>
                          </div>
                        </div>
                      ))}
                    </div>

                    {/* 进度条 */}
                    {p.total > 0 && (
                      <>
                        <div className="w-full bg-slate-200 dark:bg-slate-700 rounded-full h-2 mb-1">
                          <div className="bg-blue-500 h-2 rounded-full transition-all duration-500"
                            style={{ width: `${Math.round((p.success + p.failed) / p.total * 100)}%` }} />
                        </div>
                        <div className="flex justify-between text-xs text-slate-400">
                          <span>进度 {Math.round((p.success + p.failed) / p.total * 100)}%</span>
                          {task.batch_id && <span>批次：{task.batch_id.slice(0, 8)}...</span>}
                        </div>
                      </>
                    )}

                    {!isFinished && <p className="text-xs text-slate-400 mt-2">每 3 秒自动刷新</p>}

                    {/* 账号状态 */}
                    {p.account_statuses.length > 0 && (
                      <div className="mt-4 border-t border-slate-200 dark:border-slate-700 pt-4">
                        <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-3">账号自动获取商品状态</h3>
                        <div className="space-y-2 max-h-72 overflow-y-auto">
                          {p.account_statuses.map(as => (
                            <div key={as.account_id} className="rounded-xl border border-slate-200 dark:border-slate-700 p-3 bg-slate-50/80 dark:bg-slate-800/60">
                              <div className="flex items-center justify-between gap-2">
                                <div className="min-w-0">
                                  <div className="text-sm font-medium text-slate-800 dark:text-slate-100 truncate">
                                    {as.account_id}
                                  </div>
                                </div>
                                <span className={getSyncStatusClassName(as.sync_status)}>
                                  {getSyncStatusLabel(as.sync_status)}
                                </span>
                              </div>
                              <div className="grid grid-cols-4 gap-2 mt-2 text-xs">
                                <div className="rounded-lg bg-white dark:bg-slate-900 px-2 py-1.5">
                                  <div className="text-slate-400">总数</div>
                                  <div className="font-semibold">{as.total}</div>
                                </div>
                                <div className="rounded-lg bg-white dark:bg-slate-900 px-2 py-1.5">
                                  <div className="text-slate-400">成功</div>
                                  <div className="font-semibold text-emerald-600">{as.success}</div>
                                </div>
                                <div className="rounded-lg bg-white dark:bg-slate-900 px-2 py-1.5">
                                  <div className="text-slate-400">失败</div>
                                  <div className="font-semibold text-amber-600">{as.failed}</div>
                                </div>
                                <div className="rounded-lg bg-white dark:bg-slate-900 px-2 py-1.5">
                                  <div className="text-slate-400">待处理</div>
                                  <div className="font-semibold text-blue-600">{as.publishing + as.pending}</div>
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </motion.div>
            )
          })}
        </div>
      )}

      {/* 新建/编辑弹窗 */}
      <AnimatePresence>
        {showForm && (
          <ScheduleFormModal
            initial={editTarget}
            onClose={() => { setShowForm(false); setEditTarget(null) }}
            onSaved={() => { setShowForm(false); setEditTarget(null); loadSchedules(page, pageSize) }}
          />
        )}
      </AnimatePresence>

      {/* 删除确认 */}
      <ConfirmModal
        isOpen={!!deleteConfirm}
        title="确认删除"
        message={`确认删除定时规则「${deleteConfirm?.name ?? ''}」？关联的执行记录将保留。`}
        confirmText="删除"
        type="danger"
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => setDeleteConfirm(null)}
      />

      {/* 批量删除确认 */}
      <ConfirmModal
        isOpen={batchDeleteConfirm}
        title="确认批量删除"
        message={`确认删除选中的 ${selectedIds.length} 条定时规则？关联的执行记录将保留。`}
        confirmText={`删除 ${selectedIds.length} 条`}
        type="danger"
        loading={batchDeleting}
        onConfirm={handleBatchDelete}
        onCancel={() => setBatchDeleteConfirm(false)}
      />

      {/* 清空执行日志确认 */}
      <ConfirmModal
        isOpen={showClearLogsConfirm}
        title="确认清空日志"
        message={
          clearDays > 0
            ? `确认清空 ${clearDays} 天前的定时历史（发布与下架执行日志，保留最近 ${clearDays} 天）？此操作不可撤销。`
            : '确认清空所有定时历史（发布与下架执行日志）？此操作不可撤销。'
        }
        confirmText="清空"
        type="danger"
        loading={clearingLogs}
        onConfirm={handleClearLogs}
        onCancel={() => setShowClearLogsConfirm(false)}
      />

      {/* 批量删除执行记录确认 */}
      <ConfirmModal
        isOpen={batchDeleteLogsConfirm}
        title="确认批量删除"
        message={`确认删除选中的 ${selectedLogKeys.length} 条执行记录？此操作不可撤销。`}
        confirmText={`删除 ${selectedLogKeys.length} 条`}
        type="danger"
        loading={batchDeletingLogs}
        onConfirm={handleBatchDeleteLogs}
        onCancel={() => setBatchDeleteLogsConfirm(false)}
      />
    </div>
  )
}

export default ScheduledPublish
