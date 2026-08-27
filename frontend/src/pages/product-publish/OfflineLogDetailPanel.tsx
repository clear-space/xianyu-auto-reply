/**
 * 下架执行明细面板（detail_json 展开）
 *
 * 定时历史合并视图共用：发布记录用 LogDetailPanel，下架记录用本面板。
 * 展示风格与发布侧对齐：顶部徽章行 + 每商品一行（结果徽章 + 编号 + 权重徽章 + 标题）。
 */
import type { OfflineLogDetail } from '@/api/offlineSchedules'

const ACCOUNT_STATUS_CONFIG: Record<string, { label: string; cls: string }> = {
  success: { label: '成功', cls: 'badge-success' },
  partial: { label: '部分成功', cls: 'badge-warning' },
  failed: { label: '失败', cls: 'badge-danger' },
  account_error: { label: '账号错误', cls: 'badge-danger' },
}

const ITEM_RESULT_CONFIG: Record<string, { label: string; cls: string }> = {
  success: { label: '成功', cls: 'badge-success' },
  failed: { label: '失败', cls: 'badge-danger' },
  skipped: { label: '跳过', cls: 'badge-gray' },
}

export function OfflineLogDetailPanel({ detail }: { detail: OfflineLogDetail }) {
  const accounts = detail.accounts || []
  const missing = detail.missing_accounts || []
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap gap-2 items-center">
        {detail.algorithm_name && (
          <span className="badge-gray">算法：{detail.algorithm_name}</span>
        )}
        {detail.sample_mode === 'top' && (
          <span className="badge-gray">按权重直选</span>
        )}
        {detail.max_count != null && (
          <span className="badge-gray">每账号限 {detail.max_count} 个</span>
        )}
        {detail.detail_truncated && (
          <span className="badge-warning">商品过多，仅展示账号结果计数</span>
        )}
      </div>
      {accounts.map((a, i) => {
        const cfg = ACCOUNT_STATUS_CONFIG[a.status] || { label: a.status, cls: 'badge-gray' }
        const items = a.items || []
        return (
          <div key={i} className="rounded-xl border border-slate-200 dark:border-slate-700 p-3 bg-slate-50/80 dark:bg-slate-800/60">
            <div className="flex items-center gap-2 flex-wrap mb-1">
              <span className={cfg.cls}>{cfg.label}</span>
              <span className="font-medium text-slate-700 dark:text-slate-200">{a.account_id}</span>
              <span className="text-xs text-slate-400">
                成功 {a.suc_count} · 失败 {a.fail_count} · {items.length} 个商品
              </span>
              {a.error && <span className="text-xs text-red-500">{a.error}</span>}
            </div>
            <div className="space-y-1">
              {items.map((it, j) => {
                const icfg = ITEM_RESULT_CONFIG[it.result] || { label: it.result, cls: 'badge-gray' }
                return (
                  <div key={j} className="flex flex-wrap items-center gap-2">
                    <span className={icfg.cls}>{icfg.label}</span>
                    <span className="font-mono text-slate-500">{it.item_no ?? it.item_id.slice(0, 12)}</span>
                    {it.weight != null && <span className="badge-info text-xs">权重 {it.weight}</span>}
                    <span className="truncate max-w-[280px] text-slate-700 dark:text-slate-200" title={it.title}>{it.title}</span>
                    {it.note && <span className="text-xs text-slate-400">{it.note}</span>}
                    {it.error && <span className="text-xs text-red-400">{it.error}</span>}
                  </div>
                )
              })}
            </div>
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
