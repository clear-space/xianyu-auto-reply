/**
 * 定时发布管理页面
 *
 * 功能：
 * 1. Tab 1 - 定时规则列表：查看/新建/编辑/删除/开关/手动触发
 * 2. Tab 2 - 执行历史：查看所有规则的历史执行记录
 */
import { useState, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Clock, History, Plus, Pencil, Trash2, Play, Power, PowerOff, RefreshCw, ChevronLeft, ChevronRight, Loader2, X } from 'lucide-react'
import { useUIStore } from '@/store/uiStore'
import { getSchedules, deleteSchedule, toggleSchedule, triggerSchedule, getAllScheduleLogs, type PublishSchedule, type PublishScheduleLog } from '@/api/productPublish'
import { PageLoading } from '@/components/common/Loading'
import { ConfirmModal } from '@/components/common/ConfirmModal'
import { ScheduleFormModal } from './ScheduleFormModal'

type Tab = 'rules' | 'history'

const MODE_LABELS: Record<string, string> = { once: '单次', daily: '每天', weekly: '每周' }
const STATUS_CONFIG: Record<string, { label: string; cls: string }> = {
  pending:   { label: '待执行', cls: 'badge-gray' },
  running:   { label: '执行中', cls: 'badge-warning' },
  completed: { label: '已完成', cls: 'badge-success' },
  failed:    { label: '失败',   cls: 'badge-danger' },
  cancelled: { label: '已取消', cls: 'badge-gray' },
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
  const [tab, setTab] = useState<Tab>('rules')
  const [loading, setLoading] = useState(true)

  // 规则列表
  const [schedules, setSchedules] = useState<PublishSchedule[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(0)
  const [showForm, setShowForm] = useState(false)
  const [editTarget, setEditTarget] = useState<PublishSchedule | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<PublishSchedule | null>(null)
  const [deleting, setDeleting] = useState(false)

  // 历史记录
  const [logs, setLogs] = useState<PublishScheduleLog[]>([])
  const [logTotal, setLogTotal] = useState(0)
  const [logPage, setLogPage] = useState(1)
  const [logTotalPages, setLogTotalPages] = useState(0)

  const loadSchedules = useCallback(async (p = page) => {
    try {
      const res = await getSchedules(p, 20)
      if (res.success) {
        setSchedules(res.data.list)
        setTotal(res.data.total)
        setTotalPages(res.data.total_pages)
      }
    } catch { /* ignore */ }
  }, [page])

  const loadLogs = useCallback(async (p = logPage) => {
    try {
      const res = await getAllScheduleLogs(p, 20)
      if (res.success) {
        setLogs(res.data.list)
        setLogTotal(res.data.total)
        setLogTotalPages(res.data.total_pages)
      }
    } catch { /* ignore */ }
  }, [logPage])

  useEffect(() => {
    setLoading(true)
    loadSchedules().finally(() => setLoading(false))
  }, [loadSchedules])

  useEffect(() => {
    if (tab === 'history') loadLogs()
  }, [tab, loadLogs])

  const handleRefresh = () => {
    loadSchedules(page)
    if (tab === 'history') loadLogs(logPage)
  }

  const handleToggle = async (s: PublishSchedule) => {
    try {
      const res = await toggleSchedule(s.id)
      if (res.success) {
        addToast({ type: 'success', message: res.message })
        loadSchedules(page)
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
        loadSchedules(page)
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
        loadSchedules(page)
      } else {
        addToast({ type: 'error', message: res.message || '删除失败' })
      }
    } catch { addToast({ type: 'error', message: '删除失败' }) }
    finally { setDeleting(false) }
  }

  if (loading) return <PageLoading />

  return (
    <div className="space-y-3 sm:space-y-4">
      {/* 标题栏 */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="page-title">定时发布</h1>
          <p className="page-description">设置定时规则，到时间自动触发批量发布</p>
        </div>
        <div className="flex gap-2">
          <button className="btn-ios-secondary" onClick={handleRefresh}>
            <RefreshCw className="w-4 h-4" />刷新
          </button>
          <button className="btn-ios-primary" onClick={() => { setEditTarget(null); setShowForm(true) }}>
            <Plus className="w-4 h-4" />新建规则
          </button>
        </div>
      </div>

      {/* Tab 切换 */}
      <div className="flex gap-1 bg-slate-100 dark:bg-slate-800 p-1 rounded-lg w-fit">
        {([
          { key: 'rules', label: '定时规则', icon: Clock },
          { key: 'history', label: '执行历史', icon: History },
        ] as { key: Tab; label: string; icon: any }[]).map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              tab === t.key
                ? 'bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 shadow-sm'
                : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
            }`}>
            <t.icon className="w-4 h-4" />
            {t.label}
            {t.key === 'rules' && total > 0 && (
              <span className="text-xs bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-400 px-1.5 py-0.5 rounded-full">{total}</span>
            )}
          </button>
        ))}
      </div>

      {/* Tab 1: 定时规则列表 */}
      {tab === 'rules' && (
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
                  <tr><td colSpan={7} className="text-center py-12 text-slate-400">
                    <div className="flex flex-col items-center gap-2"><Clock className="w-12 h-12 text-slate-300" />
                      <p>暂无定时规则，点击「新建规则」开始</p></div>
                  </td></tr>
                ) : schedules.map(s => (
                  <tr key={s.id}>
                    <td>
                      <span className="font-medium text-slate-800 dark:text-slate-100 truncate block max-w-[180px]" title={s.name}>{s.name}</span>
                    </td>
                    <td><span className="badge-gray">{MODE_LABELS[s.schedule_mode] || s.schedule_mode}</span></td>
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
          {totalPages > 1 && (
            <div className="flex-shrink-0 flex items-center justify-between px-4 py-3 border-t border-slate-200 dark:border-slate-700">
              <span className="text-sm text-slate-500">{total} 条，第 {page}/{totalPages} 页</span>
              <div className="flex gap-1">
                <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1}
                  className="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-50">
                  <ChevronLeft className="w-4 h-4" /></button>
                <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages}
                  className="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-50">
                  <ChevronRight className="w-4 h-4" /></button>
              </div>
            </div>
          )}
        </motion.div>
      )}

      {/* Tab 2: 执行历史 */}
      {tab === 'history' && (
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
          className="vben-card flex flex-col" style={{ height: 'calc(100vh - 260px)', minHeight: '400px' }}>
          <div className="vben-card-header">
            <h2 className="vben-card-title"><History className="w-4 h-4" />执行历史</h2>
            <span className="badge-primary">共 {logTotal} 条</span>
          </div>
          <div className="flex-1 overflow-x-auto overflow-y-auto">
            <table className="table-ios">
              <thead className="sticky top-0 bg-white dark:bg-slate-800 z-10">
                <tr>
                  <th>计划时间</th>
                  <th>执行时间</th>
                  <th>批次ID</th>
                  <th>结果</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {logs.length === 0 ? (
                  <tr><td colSpan={5} className="text-center py-12 text-slate-400">
                    <div className="flex flex-col items-center gap-2"><History className="w-12 h-12 text-slate-300" />
                      <p>暂无执行记录</p></div>
                  </td></tr>
                ) : logs.map(l => (
                  <tr key={l.id}>
                    <td className="text-sm whitespace-nowrap">
                      {new Date(l.scheduled_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    </td>
                    <td className="text-sm whitespace-nowrap text-slate-500">
                      {l.executed_at ? new Date(l.executed_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '-'}
                    </td>
                    <td className="text-sm text-slate-500 font-mono">
                      {l.batch_id ? l.batch_id.slice(0, 12) + '...' : '-'}
                    </td>
                    <td className="text-sm">
                      <span className="text-green-600">{l.success_count} 成功</span>
                      {l.failed_count > 0 && <span className="text-red-500 ml-1">/ {l.failed_count} 失败</span>}
                      <span className="text-slate-400 ml-1">/ {l.total_count} 总</span>
                    </td>
                    <td>
                      <span className={STATUS_CONFIG[l.status]?.cls || 'badge-gray'}>
                        {STATUS_CONFIG[l.status]?.label || l.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {logTotalPages > 1 && (
            <div className="flex-shrink-0 flex items-center justify-between px-4 py-3 border-t border-slate-200 dark:border-slate-700">
              <span className="text-sm text-slate-500">{logTotal} 条，第 {logPage}/{logTotalPages} 页</span>
              <div className="flex gap-1">
                <button onClick={() => setLogPage(p => Math.max(1, p - 1))} disabled={logPage <= 1}
                  className="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-50">
                  <ChevronLeft className="w-4 h-4" /></button>
                <button onClick={() => setLogPage(p => Math.min(logTotalPages, p + 1))} disabled={logPage >= logTotalPages}
                  className="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-50">
                  <ChevronRight className="w-4 h-4" /></button>
              </div>
            </div>
          )}
        </motion.div>
      )}

      {/* 新建/编辑弹窗 */}
      <AnimatePresence>
        {showForm && (
          <ScheduleFormModal
            initial={editTarget}
            onClose={() => { setShowForm(false); setEditTarget(null) }}
            onSaved={() => { setShowForm(false); setEditTarget(null); loadSchedules(page) }}
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
    </div>
  )
}

export default ScheduledPublish
