/**
 * 自动下架规则列表
 *
 * 功能：
 * 1. 规则列表：查看/新建/编辑/删除/批量删除/开关/手动触发
 * 2. 删除与批量删除均二次确认
 * 3. 规则总数通过 onTotalChange 上报（供 Tab 角标展示）
 */
import { useState, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Plus, Pencil, Trash2, Play, Power, PowerOff, RefreshCw, ChevronLeft, ChevronRight, PackageX } from 'lucide-react'
import { useUIStore } from '@/store/uiStore'
import {
  getOfflineSchedules, deleteOfflineSchedule, toggleOfflineSchedule, triggerOfflineSchedule,
  type OfflineSchedule,
} from '@/api/offlineSchedules'
import { PageLoading } from '@/components/common/Loading'
import { ConfirmModal } from '@/components/common/ConfirmModal'
import { OfflineScheduleFormModal } from './OfflineScheduleFormModal'

interface Props {
  /** 规则总数变化回调（供父组件 Tab 角标展示） */
  onTotalChange?: (total: number) => void
}

const MODE_LABELS: Record<string, string> = { daily: '每天', weekly: '每周' }

function formatNextTrigger(dt: string | null | undefined): string {
  if (!dt) return '-'
  const d = new Date(dt)
  const diff = d.getTime() - Date.now()
  if (diff < 0) return '已到期'
  if (diff < 60000) return '即将执行'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟后`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时后`
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function formatConfig(sc: OfflineSchedule): string {
  const cfg = sc.schedule_config
  if (cfg?.random && cfg?.time_range) {
    return `${cfg.time_range.start}~${cfg.time_range.end} 随机`
  }
  if (cfg?.times?.length) {
    return cfg.times.join(', ')
  }
  return '-'
}

export function OfflineRules({ onTotalChange }: Props) {
  const { addToast } = useUIStore()
  const [loading, setLoading] = useState(true)
  const [schedules, setSchedules] = useState<OfflineSchedule[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [totalPages, setTotalPages] = useState(0)
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [showForm, setShowForm] = useState(false)
  const [editTarget, setEditTarget] = useState<OfflineSchedule | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<OfflineSchedule | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [batchDeleteConfirm, setBatchDeleteConfirm] = useState(false)
  const [batchDeleting, setBatchDeleting] = useState(false)

  const loadSchedules = useCallback(async (p = page, size = pageSize) => {
    try {
      const res = await getOfflineSchedules(p, size)
      if (res.success) {
        setSchedules(res.data.list)
        setTotal(res.data.total)
        setTotalPages(res.data.total_pages)
        onTotalChange?.(res.data.total)
        // 清理已不在当前页的选中项
        const currentIds = new Set(res.data.list.map((s: OfflineSchedule) => s.id))
        setSelectedIds(prev => prev.filter(id => currentIds.has(id)))
      }
    } catch { /* ignore */ }
  }, [page, pageSize, onTotalChange])

  useEffect(() => {
    setLoading(true)
    loadSchedules().finally(() => setLoading(false))
  }, [loadSchedules])

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

  const allCurrentSelected = schedules.length > 0 && schedules.every(s => selectedIds.includes(s.id))

  const handleToggle = async (s: OfflineSchedule) => {
    try {
      const res = await toggleOfflineSchedule(s.id)
      if (res.success) {
        addToast({ type: 'success', message: res.message || '操作成功' })
        loadSchedules(page, pageSize)
      } else {
        addToast({ type: 'error', message: res.message || '操作失败' })
      }
    } catch { addToast({ type: 'error', message: '操作失败' }) }
  }

  const handleTrigger = async (s: OfflineSchedule) => {
    try {
      const res = await triggerOfflineSchedule(s.id)
      if (res.success) {
        addToast({ type: 'success', message: '已触发自动下架，请查看定时历史' })
      } else {
        addToast({ type: 'error', message: res.message || '触发失败' })
      }
    } catch { addToast({ type: 'error', message: '触发失败' }) }
  }

  const handleDelete = async () => {
    if (!deleteConfirm) return
    setDeleting(true)
    try {
      const res = await deleteOfflineSchedule(deleteConfirm.id)
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
        const res = await deleteOfflineSchedule(id)
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

  if (loading) return <PageLoading />

  return (
    <div className="space-y-3">
      {/* 标题栏 */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <p className="page-description">按上架时长与订单情况自动下架商品</p>
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
            <Plus className="w-4 h-4" />新建下架规则
          </button>
        </div>
      </div>

      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
        className="vben-card flex flex-col" style={{ height: 'calc(100vh - 260px)', minHeight: '400px' }}>
        <div className="vben-card-header">
          <h2 className="vben-card-title"><PackageX className="w-4 h-4" />下架规则</h2>
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
                <th>筛选参数</th>
                <th>账号</th>
                <th>下次触发</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {schedules.length === 0 ? (
                <tr><td colSpan={9} className="text-center py-12 text-slate-400">
                  <div className="flex flex-col items-center gap-2"><PackageX className="w-12 h-12 text-slate-300" />
                    <p>暂无下架规则，点击「新建下架规则」开始</p></div>
                </td></tr>
              ) : schedules.map(s => (
                <tr key={s.id} className={selectedIds.includes(s.id) ? 'bg-blue-50 dark:bg-blue-900/10' : ''}>
                  <td>
                    <input type="checkbox" checked={selectedIds.includes(s.id)}
                      onChange={() => toggleSelect(s.id)}
                      className="w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" />
                  </td>
                  <td>
                    <span className="font-medium text-slate-800 dark:text-slate-100 truncate block max-w-[160px]" title={s.name}>{s.name}</span>
                  </td>
                  <td><span className="badge-gray">{MODE_LABELS[s.schedule_mode] || s.schedule_mode}</span></td>
                  <td className="text-sm text-slate-500">{formatConfig(s)}</td>
                  <td className="text-sm text-slate-500 whitespace-nowrap">
                    上架&gt;{s.offline_days}天{s.no_order_days > 0 ? ` · 无单${s.no_order_days}天` : ' · 不查订单'} · 限{s.max_count}个
                  </td>
                  <td className="text-sm text-slate-500">{(s.account_count ?? s.account_ids.length)} 个</td>
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
              <select value={pageSize} onChange={e => { setPageSize(Number(e.target.value)); setPage(1) }}
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

      {/* 新建/编辑弹窗 */}
      <AnimatePresence>
        {showForm && (
          <OfflineScheduleFormModal
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
        message={`确认删除下架规则「${deleteConfirm?.name ?? ''}」？关联的执行记录将保留。`}
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
        message={`确认删除选中的 ${selectedIds.length} 条下架规则？关联的执行记录将保留。`}
        confirmText={`删除 ${selectedIds.length} 条`}
        type="danger"
        loading={batchDeleting}
        onConfirm={handleBatchDelete}
        onCancel={() => setBatchDeleteConfirm(false)}
      />
    </div>
  )
}

export default OfflineRules
