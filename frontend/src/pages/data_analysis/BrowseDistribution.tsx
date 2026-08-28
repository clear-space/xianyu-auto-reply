/**
 * 流量分布页面
 *
 * 独立的账号选择、时间范围和查询，展示来源分布、商品分布、时间分布、地域分布。
 * 注意：官方接口仅返回最近一天快照数据（实调验证：所有条目 ds 相同）。
 */
import { useCallback, useState } from 'react'
import { motion } from 'framer-motion'
import { getBrowseSummary, type BrowseSummaryResponse, type ProfileItem } from '@/api/data_analysis'
import { useUIStore } from '@/store/uiStore'
import { AccountTimeRangeBar } from './shared/AccountTimeRangeBar'
import { useDataQuery } from './shared/useDataQuery'

/** 单个分布卡片 */
function DistributionCard({
  title,
  items,
  labelWidth = 'w-16',
}: {
  title: string
  items: ProfileItem[]
  labelWidth?: string
}) {
  return (
    <div className="bg-white dark:bg-slate-800 rounded-lg p-4 shadow-sm border border-gray-100 dark:border-slate-700">
      <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">{title}</h3>
      <div className="h-[240px] overflow-y-auto pr-2 space-y-2.5">
        {items.map((item, idx) => (
          <div key={idx} className="flex items-center gap-2">
            <span
              className={`text-xs text-gray-600 dark:text-gray-400 ${labelWidth} flex-shrink-0 truncate`}
              title={item.profileVal}
            >
              {item.profileVal}
            </span>
            <div className="flex-1 h-5 bg-gray-100 dark:bg-slate-700 rounded overflow-hidden">
              <div
                className="h-full bg-blue-500 rounded transition-all"
                style={{ width: `${item.usrRatio * 100}%` }}
              />
            </div>
            <span className="text-xs text-gray-500 dark:text-gray-400 w-14 text-right flex-shrink-0">
              {item.usrRatioFormat}
            </span>
          </div>
        ))}
        {items.length === 0 && (
          <p className="text-xs text-gray-400 text-center py-4">暂无数据</p>
        )}
      </div>
    </div>
  )
}

export function BrowseDistribution() {
  const { addToast } = useUIStore()
  const [browseData, setBrowseData] = useState<{
    sceneSourceList: ProfileItem[]
    itemCateList: ProfileItem[]
    buyerActiveList: ProfileItem[]
    buyerProvinceList: ProfileItem[]
  } | null>(null)

  const onResult = useCallback(
    (result: BrowseSummaryResponse | null) => {
      if (result?.success && result.data) {
        setBrowseData(result.data.data || null)
      } else {
        if (result && !result.success) {
          addToast({ type: 'error', message: result.message || '获取流量分布失败' })
        }
        setBrowseData(null)
      }
    },
    [addToast],
  )

  const {
    accounts,
    accountId,
    setAccountId,
    dateType,
    setDateType,
    customStart,
    setCustomStart,
    customEnd,
    setCustomEnd,
    loading,
    query,
  } = useDataQuery<BrowseSummaryResponse>({
    fetcher: (params) => getBrowseSummary(params),
    onResult,
  })

  return (
    <div className="space-y-4">
      <AccountTimeRangeBar
        title="流量分布"
        accounts={accounts}
        accountId={accountId}
        onAccountChange={setAccountId}
        dateType={dateType}
        onDateTypeChange={setDateType}
        customStart={customStart}
        onCustomStartChange={setCustomStart}
        customEnd={customEnd}
        onCustomEndChange={setCustomEnd}
        loading={loading}
        onQuery={query}
      />

      {/* 加载中 */}
      {loading && (
        <div className="flex items-center justify-center py-8">
          <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-500"></div>
          <span className="ml-2 text-sm text-gray-500 dark:text-gray-400">加载中...</span>
        </div>
      )}

      {/* 分布图表 */}
      {!loading && browseData && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="grid grid-cols-1 lg:grid-cols-2 gap-4"
        >
          <DistributionCard title="来源分布" items={browseData.sceneSourceList || []} />
          <DistributionCard title="商品分布" items={browseData.itemCateList || []} labelWidth="w-24" />
          <DistributionCard title="时间分布" items={browseData.buyerActiveList || []} />
          <DistributionCard title="地域分布" items={browseData.buyerProvinceList || []} />
        </motion.div>
      )}

      {/* 未选择账号提示 */}
      {!accountId && (
        <div className="flex flex-col items-center justify-center py-12 text-gray-400">
          <p>请先选择一个账号</p>
        </div>
      )}
    </div>
  )
}
