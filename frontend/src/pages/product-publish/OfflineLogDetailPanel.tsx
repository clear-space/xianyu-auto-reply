/**
 * 下架执行明细面板（detail_json 展开）
 *
 * 定时历史合并视图共用：发布记录用 LogDetailPanel，下架记录用本面板。
 */
import type { OfflineLogDetail } from '@/api/offlineSchedules'

const ACCOUNT_STATUS_CONFIG: Record<string, { label: string; cls: string }> = {
  success: { label: '成功', cls: 'badge-success' },
  partial: { label: '部分成功', cls: 'badge-warning' },
  failed: { label: '失败', cls: 'badge-danger' },
  account_error: { label: '账号错误', cls: 'badge-danger' },
}

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
