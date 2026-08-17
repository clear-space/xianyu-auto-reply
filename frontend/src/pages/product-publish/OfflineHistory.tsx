/**
 * 自动下架执行历史
 *
 * 功能：
 * 1. 查看所有下架规则的执行记录
 * 2. 展开查看明细（每账号下架成功/失败商品）
 * 3. 清空日志（支持保留天数，默认10天）
 */
import { useState, useEffect, useCallback, Fragment } from 'react'
import { motion } from 'framer-motion'
import { History, RefreshCw, Trash2, ChevronLeft, ChevronRight, ChevronDown, ChevronUp, PackageX } from 'lucide-react'
import { useUIStore } from '@/store/uiStore'
import {
  getAllOfflineScheduleLogs, clearOfflineScheduleLogs,
  type OfflineScheduleLog, type OfflineLogDetail,
} from '@/api/offlineSchedules'
import { PageLoading } from '@/components/common/Loading'
import { ConfirmModal } from '@/components/common/ConfirmModal'

const STATUS_CONFIG: Record<string, { label: string; cls: string }> = {
  pending:   { label: '待执行', cls: 'badge-gray' },
  running:   { label: '执行中', cls: 'badge-warning' },
  completed: { label: '已完成', cls: 'badge-success' },
  failed:    { label: '失败',   cls: 'badge-danger' },
  cancelled: { label: '已取消', cls: 'badge-gray' },
}

const ACCOUNT_STATUS_CONFIG: Record<string, { label: string; cls: string }> = {
  success: { label: '成功', cls: 'badge-success' },
  partial: { label: '部分成功', cls: 'badge-warning' },
  failed: { label: '失败', cls: 'badge-danger' },
  account_error: { label: '账号错误', cls: 'badge-danger' },
}

/** 下架执行明细面板（detail_json 展开，定时历史合并视图共用） */
export function OfflineLogDetailPanel({ detail }: { detail: OfflineLogDetail }) {
  const accounts = detail.accounts || []
  const missing = detail.missing_accounts || []
  return (
    <div className="space-y-3 text-sm">
      {detail.offline_days != null && (
        <div className="flex flex-wrap gap-2 items-center">
          <span className="badge-gray">上架&gt;{detail.offline_days}天</span>
          <span className="badge-gray">{detail.no_order_days ? `无单${detail.no_order_days}天` : '不检查订单'}</span>
          <span className="badge-gray">每账号限 {detail.max_count ?? '-'} 个</span>
          {detail.detail_truncated && <span className="badge-warning">商品过多，仅展示账号结果计数</span>}
        </div>
      )}
      {accounts.map((a, i) => {
        const cfg = ACCOUNT_STATUS_CONFIG[a.status] || { label: a.status, cls: 'badge-gray' }
        const items = a.items || []
        return (
          <div key={i} className="rounded-xl border border-slate-200 dark:border-slate-700 p-3 bg-slate-50/80 dark:bg-slate-800/60">
            <div className="flex items-center gap-2 flex-wrap">
              <span className={cfg.cls}>{cfg.label}</span>
              <span className="font-medium text-slate-700 dark:text-slate-200">{a.account_id}</span>
              <span className="text-xs text-slate-400">
                成功 {a.suc_count} · 失败 {a.fail_count}
              </span>
              {a.error && <span className="text-xs text-red-500">{a.error}</span>}
            </div>
            {items.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-2">
                {items.map((it, j) => (
                  <span key={j} className="badge-gray text-xs" title={it.error || it.note}>
                    {it.item_id.slice(0, 16)}
                    {it.result === 'success' ? ' ✓' : it.result === 'skipped' ? ' ⏭' : ' ✗'}
                  </span>
                ))}
              </div>
            )}
          </div>
        )
      })}
      {missing.length > 0 && (
        <div className="text-xs text-red-500">规则中以下账号不存在或无权使用：{missing.join('、')}</div>
      )}
      {accounts.length === 0 && <p className="text-slate-400">暂无明细</p>}
    </div>
  )
}

export function OfflineHistory() {
  const { addToast } = useUIStore()
  const [loading, setLoading] = useState(true)
  const [logs, setLogs] = useState<OfflineScheduleLog[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(0)
  const [expandedLogIds, setExpandedLogIds] = useState<Set<number>>(new Set())
  const [showClearConfirm, setShowClearConfirm] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [clearDays, setClearDays] = useState(10) // 保留天数，0=清空全部，默认10天

  const loadLogs = useCallback(async (p = page) => {
    try {
      const res = await getAllOfflineScheduleLogs(p, 20)
      if (res.success) {
        setLogs(res.data.list)
        setTotal(res.data.total)
        setTotalPages(res.data.total_pages)
      }
    } catch { /* ignore */ }
  }, [page])

  useEffect(() => {
    setLoading(true)
    loadLogs().finally(() => setLoading(false))
  }, [loadLogs])

  const toggleExpand = (id: number) => {
    setExpandedLogIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleClearLogs = async () => {
    setClearing(true)
    try {
      const res = await clearOfflineScheduleLogs(clearDays > 0 ? clearDays : undefined)
      if (res.success) {
        addToast({ type: 'success', message: res.message || '清空成功' })
        setShowClearConfirm(false)
        if (page === 1) await loadLogs(1)
        else setPage(1)
      } else {
        addToast({ type: 'error', message: res.message || '清空失败' })
      }
    } catch {
      addToast({ type: 'error', message: '清空失败' })
    } finally {
      setClearing(false)
    }
  }

  if (loading) return <PageLoading />

  return (
    <div className="space-y-3">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <p className="page-description">查看自动下架规则的执行结果</p>
        </div>
        <div className="flex items-center gap-2">
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
            onClick={() => setShowClearConfirm(true)}
            className="btn-ios-danger btn-sm"
            title={clearDays > 0 ? `清空${clearDays}天前的执行日志` : '清空全部执行日志'}
            disabled={clearing}
          >
            <Trash2 className="w-3.5 h-3.5" />清空日志
          </button>
          <button className="btn-ios-secondary btn-sm" onClick={() => loadLogs(page)} disabled={clearing}>
            <RefreshCw className="w-3.5 h-3.5" />刷新
          </button>
          <span className="badge-primary">共 {total} 条</span>
        </div>
      </div>

      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
        className="vben-card flex flex-col" style={{ height: 'calc(100vh - 260px)', minHeight: '400px' }}>
        <div className="vben-card-header">
          <h2 className="vben-card-title"><History className="w-4 h-4" />执行历史</h2>
        </div>
        <div className="flex-1 overflow-x-auto overflow-y-auto">
          <table className="table-ios">
            <thead className="sticky top-0 bg-white dark:bg-slate-800 z-10">
              <tr>
                <th>规则名</th>
                <th>计划时间</th>
                <th>执行时间</th>
                <th>结果</th>
                <th>状态</th>
                <th>明细</th>
              </tr>
            </thead>
            <tbody>
              {logs.length === 0 ? (
                <tr><td colSpan={6} className="text-center py-12 text-slate-400">
                  <div className="flex flex-col items-center gap-2"><PackageX className="w-12 h-12 text-slate-300" />
                    <p>暂无执行记录</p></div>
                </td></tr>
              ) : logs.map(l => (
                <Fragment key={l.id}>
                  <tr className={expandedLogIds.has(l.id) ? 'bg-blue-50 dark:bg-blue-900/10' : ''}>
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
                      <span className="text-slate-400 ml-1">/ {l.total_count} 候选</span>
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
                        onClick={() => toggleExpand(l.id)}
                        disabled={!l.detail_json}
                        className="p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed text-slate-500"
                        title={l.detail_json ? '展开明细' : '暂无明细'}
                      >
                        {expandedLogIds.has(l.id) ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                      </button>
                    </td>
                  </tr>
                  {expandedLogIds.has(l.id) && l.detail_json && (
                    <tr>
                      <td colSpan={6} className="bg-slate-50 dark:bg-slate-800/60 px-4 py-3 border-t border-slate-200 dark:border-slate-700">
                        <OfflineLogDetailPanel detail={l.detail_json} />
                      </td>
                    </tr>
                  )}
                </Fragment>
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

      <ConfirmModal
        isOpen={showClearConfirm}
        title="确认清空日志"
        message={
          clearDays > 0
            ? `确认清空 ${clearDays} 天前的下架执行日志（保留最近 ${clearDays} 天）？此操作不可撤销。`
            : '确认清空所有下架执行日志？此操作不可撤销。'
        }
        confirmText="清空"
        type="danger"
        loading={clearing}
        onConfirm={handleClearLogs}
        onCancel={() => setShowClearConfirm(false)}
      />
    </div>
  )
}

export default OfflineHistory
