/**
 * 账号 + 时间范围查询栏（数据罗盘各页面通用）
 *
 * 纯展示组件：账号列表、状态由 useDataQuery 提供。
 */
import { RefreshCw } from 'lucide-react'
import type { AccountDetail } from '@/types'
import { DATE_TYPE_OPTIONS, type DateTypeValue } from './useDataQuery'

interface AccountTimeRangeBarProps {
  title: string
  accounts: AccountDetail[]
  accountId: number | null
  onAccountChange: (id: number) => void
  dateType: DateTypeValue
  onDateTypeChange: (t: DateTypeValue) => void
  customStart: string
  onCustomStartChange: (v: string) => void
  customEnd: string
  onCustomEndChange: (v: string) => void
  loading: boolean
  onQuery: () => void
  /** 标题右侧附加内容（如数据区间展示） */
  extra?: React.ReactNode
}

export function AccountTimeRangeBar({
  title,
  accounts,
  accountId,
  onAccountChange,
  dateType,
  onDateTypeChange,
  customStart,
  onCustomStartChange,
  customEnd,
  onCustomEndChange,
  loading,
  onQuery,
  extra,
}: AccountTimeRangeBarProps) {
  return (
    <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 bg-white dark:bg-slate-800 rounded-lg p-4 shadow-sm">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold text-gray-800 dark:text-gray-200">{title}</h2>
        {extra}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {/* 账号选择 */}
        <select
          className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-slate-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={accountId ?? ''}
          onChange={(e) => onAccountChange(Number(e.target.value))}
        >
          <option value="" disabled>选择账号</option>
          {[...accounts]
            .sort((a, b) => (a.enabled === b.enabled ? 0 : a.enabled ? -1 : 1))
            .map((acc) => (
              <option key={acc.pk} value={acc.pk}>
                {acc.note || acc.id || `账号${acc.pk}`}{acc.enabled ? '' : '（已禁用）'}
              </option>
            ))}
        </select>

        {/* 时间范围选择 */}
        <div className="flex rounded-md overflow-hidden border border-gray-300 dark:border-gray-600">
          {DATE_TYPE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              className={`px-3 py-1.5 text-sm transition-colors ${
                dateType === opt.value
                  ? 'bg-blue-500 text-white'
                  : 'bg-white dark:bg-slate-700 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-slate-600'
              }`}
              onClick={() => onDateTypeChange(opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {/* 自定义日期范围选择器 */}
        {dateType === 'customDate' && (
          <div className="flex items-center gap-2">
            <input
              type="date"
              className="px-2 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-slate-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
              value={customStart}
              onChange={(e) => onCustomStartChange(e.target.value)}
            />
            <span className="text-gray-400 text-sm">至</span>
            <input
              type="date"
              className="px-2 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-slate-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
              value={customEnd}
              onChange={(e) => onCustomEndChange(e.target.value)}
            />
            <button
              className="px-3 py-1.5 text-sm bg-blue-500 text-white rounded-md hover:bg-blue-600 transition-colors disabled:opacity-50"
              onClick={onQuery}
              disabled={loading || !accountId || !customStart || !customEnd}
            >
              查询
            </button>
          </div>
        )}

        {/* 刷新按钮 */}
        <button
          className="p-1.5 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-slate-700 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-slate-600 transition-colors disabled:opacity-50"
          onClick={onQuery}
          disabled={loading || !accountId}
          title="刷新数据"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>
    </div>
  )
}
