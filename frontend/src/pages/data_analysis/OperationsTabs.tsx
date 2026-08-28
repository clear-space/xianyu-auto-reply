/**
 * 经营数据页（复购 / 退款 / 粉丝 / 客服 四个 Tab）
 *
 * 四个模块共用账号 + 时间范围查询栏，切换 Tab 时按需拉取对应接口：
 * - 复购 repurchase.summary：10 个指标（无趋势图）
 * - 退款 refund.summary：34 个指标分 5 组（金额裸数值 + 笔数/比率 banner 结构）
 * - 粉丝 fans.summary：3 banner + 趋势图
 * - 客服 cs.overview.summary：14 指标 + 趋势图
 */
import { useCallback, useState } from 'react'
import { TrendingDown, TrendingUp } from 'lucide-react'
import {
  getCsOverviewSummary,
  getFansSummary,
  getRefundSummary,
  getRepurchaseSummary,
  type BannerDataItem,
  type GraphDataItem,
  type OverviewModuleResponse,
  type RefundMetricValue,
  type RefundSummaryResponse,
} from '@/api/data_analysis'
import {
  CS_GROUPS,
  FANS_GROUPS,
  REFUND_GROUPS,
  REPURCHASE_GROUPS,
  formatBannerValue,
  type RefundMetricDef,
} from './metrics'
import { AccountTimeRangeBar } from './shared/AccountTimeRangeBar'
import { MetricBannerGrid } from './shared/MetricBannerGrid'
import { MetricTrendChart, formatGraphDate } from './shared/MetricTrendChart'
import { useDataQuery, type DataQueryParams } from './shared/useDataQuery'
import { useUIStore } from '@/store/uiStore'

type TabKey = 'repurchase' | 'refund' | 'fans' | 'cs'

const TABS: { key: TabKey; label: string }[] = [
  { key: 'repurchase', label: '复购' },
  { key: 'refund', label: '退款' },
  { key: 'fans', label: '粉丝' },
  { key: 'cs', label: '客服' },
]

/** 退款统计瓦片组 */
function RefundStatGrid({
  data,
  metricDefs,
}: {
  data: Record<string, RefundMetricValue>
  metricDefs: RefundMetricDef[]
}) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-3">
      {metricDefs.map((def) => {
        const raw = data[def.name]
        let value: string
        let ratioFormat: string | undefined
        let lastDataStr: string | undefined
        let ratio: number | undefined
        let isItem = false

        if (def.kind === 'amount') {
          const num = typeof raw === 'number' ? raw : Number(raw ?? 0)
          value = `¥${num.toFixed(2)}`
        } else {
          const item = raw as BannerDataItem | undefined
          isItem = true
          value = formatBannerValue(item)
          ratioFormat = item?.ratioFormat
          lastDataStr = item?.lastDataStr
          ratio = item?.ratio
        }

        const isUp = ratio != null && ratio > 0
        const isDown = ratio != null && ratio < 0

        return (
          <div
            key={def.name}
            className="bg-white dark:bg-slate-800 rounded-lg p-3 shadow-sm border border-gray-100 dark:border-slate-700"
          >
            <div className="flex items-center justify-between mb-1.5">
              <span
                className="text-xs text-gray-500 dark:text-gray-400 truncate"
                title={def.uncertain ? `${def.label}（名称按字段语义推断）` : def.label}
              >
                {def.label}
                {def.uncertain && <span className="text-gray-300 dark:text-gray-600">?</span>}
              </span>
            </div>
            <div className="flex items-end justify-between">
              <span className="text-xl font-bold text-gray-800 dark:text-gray-100">{value}</span>
              {isItem && (
                <div className="text-right min-w-[52px]">
                  {ratioFormat && ratioFormat !== '-' ? (
                    <span
                      className={`text-xs flex items-center gap-0.5 justify-end ${
                        isUp ? 'text-green-500' : isDown ? 'text-red-500' : 'text-gray-400'
                      }`}
                    >
                      {isUp && <TrendingUp className="w-3 h-3" />}
                      {isDown && <TrendingDown className="w-3 h-3" />}
                      {isUp ? '+' : ''}{ratioFormat}
                    </span>
                  ) : (
                    <span className="text-xs text-gray-400">--</span>
                  )}
                  {lastDataStr && lastDataStr !== '-' && (
                    <div className="text-xs text-gray-400 mt-0.5">上周期 {lastDataStr}</div>
                  )}
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

/** 概览型 Tab 响应解析（复购/粉丝/客服同构） */
function parseOverviewResult(result: OverviewModuleResponse | null) {
  if (result?.success && result.data) {
    const gbb = result.data.data?.graphBannerBenchData
    return {
      banners: gbb?.bannerDataList || [],
      graph: gbb?.graphDataList || [],
      range: result.data.extendInfo?.realDateRange || [],
      ok: true,
      error: null as string | null,
    }
  }
  return {
    banners: [],
    graph: [],
    range: [],
    ok: false,
    error: (result && !result.success ? result.message : null) as string | null,
  }
}

export function OperationsTabs() {
  const { addToast } = useUIStore()
  const [tab, setTab] = useState<TabKey>('repurchase')

  // 各 Tab 数据（切换后保留）
  const [repurchase, setRepurchase] = useState<{ banners: BannerDataItem[]; range: string[] }>({
    banners: [], range: [],
  })
  const [refund, setRefund] = useState<{
    data: Record<string, RefundMetricValue>; range: string[]
  }>({ data: {}, range: [] })
  const [fans, setFans] = useState<{
    banners: BannerDataItem[]; graph: GraphDataItem[]; range: string[]
  }>({ banners: [], graph: [], range: [] })
  const [cs, setCs] = useState<{
    banners: BannerDataItem[]; graph: GraphDataItem[]; range: string[]
  }>({ banners: [], graph: [], range: [] })
  const [fansMetric, setFansMetric] = useState('totalFansCnt')
  const [csMetric, setCsMetric] = useState('consultUv')

  /** 按当前 Tab 分发请求 */
  const fetcher = useCallback(
    (params: DataQueryParams) => {
      switch (tab) {
        case 'repurchase':
          return getRepurchaseSummary(params)
        case 'refund':
          return getRefundSummary(params)
        case 'fans':
          return getFansSummary(params)
        case 'cs':
          return getCsOverviewSummary(params)
      }
    },
    [tab],
  )

  /** 按当前 Tab 分发响应 */
  const onResult = useCallback(
    (result: OverviewModuleResponse | RefundSummaryResponse | null) => {
      if (tab === 'refund') {
        const r = result as RefundSummaryResponse | null
        if (r?.success && r.data) {
          setRefund({ data: r.data.data || {}, range: r.data.extendInfo?.realDateRange || [] })
        } else {
          if (r && !r.success) addToast({ type: 'error', message: r.message || '获取退款数据失败' })
          setRefund({ data: {}, range: [] })
        }
        return
      }
      const parsed = parseOverviewResult(result as OverviewModuleResponse | null)
      if (parsed.error) addToast({ type: 'error', message: parsed.error })
      if (tab === 'repurchase') setRepurchase({ banners: parsed.banners, range: parsed.range })
      if (tab === 'fans') setFans({ banners: parsed.banners, graph: parsed.graph, range: parsed.range })
      if (tab === 'cs') setCs({ banners: parsed.banners, graph: parsed.graph, range: parsed.range })
    },
    [tab, addToast],
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
  } = useDataQuery<OverviewModuleResponse | RefundSummaryResponse>({
    fetcher,
    onResult,
    refreshKey: tab,
  })

  /** 标题右侧的当前 Tab 数据区间 */
  const activeRange =
    tab === 'repurchase' ? repurchase.range : tab === 'refund' ? refund.range : tab === 'fans' ? fans.range : cs.range
  const rangeLabel =
    activeRange.length === 2
      ? `${formatGraphDate(activeRange[0]).replace('/', '-')} ~ ${formatGraphDate(activeRange[1]).replace('/', '-')}`
      : null

  const hasData =
    tab === 'repurchase'
      ? repurchase.banners.length > 0
      : tab === 'refund'
        ? Object.keys(refund.data).length > 0
        : tab === 'fans'
          ? fans.banners.length > 0
          : cs.banners.length > 0

  return (
    <div className="space-y-4">
      <AccountTimeRangeBar
        title="经营数据"
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
        extra={
          rangeLabel ? (
            <span className="text-xs text-gray-400 dark:text-gray-500">数据区间 {rangeLabel}</span>
          ) : undefined
        }
      />

      {/* Tab 切换 */}
      <div className="flex rounded-md overflow-hidden border border-gray-300 dark:border-gray-600 w-fit">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`px-4 py-1.5 text-sm transition-colors ${
              tab === t.key
                ? 'bg-blue-500 text-white'
                : 'bg-white dark:bg-slate-800 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-slate-700'
            }`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* 加载遮罩 */}
      {loading && (
        <div className="flex items-center justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
          <span className="ml-3 text-gray-500 dark:text-gray-400">加载中...</span>
        </div>
      )}

      {/* 复购 */}
      {!loading && tab === 'repurchase' && repurchase.banners.length > 0 && (
        <MetricBannerGrid groups={REPURCHASE_GROUPS} bannerData={repurchase.banners} selectable={false} />
      )}

      {/* 退款 */}
      {!loading && tab === 'refund' && Object.keys(refund.data).length > 0 && (
        <div className="space-y-4">
          {REFUND_GROUPS.map((group) => (
            <div key={group.key}>
              <h3 className="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2 flex items-center gap-1.5">
                <span className="w-1 h-3.5 rounded bg-blue-500 inline-block" />
                {group.label}
              </h3>
              <RefundStatGrid data={refund.data} metricDefs={group.metrics} />
            </div>
          ))}
        </div>
      )}

      {/* 粉丝 */}
      {!loading && tab === 'fans' && fans.banners.length > 0 && (
        <>
          <MetricBannerGrid
            groups={FANS_GROUPS}
            bannerData={fans.banners}
            selectedMetric={fansMetric}
            onMetricClick={setFansMetric}
          />
          {fans.graph.length > 0 && (
            <MetricTrendChart
              graphData={fans.graph}
              metric={fansMetric}
              onMetricChange={setFansMetric}
              groups={FANS_GROUPS}
            />
          )}
        </>
      )}

      {/* 客服 */}
      {!loading && tab === 'cs' && cs.banners.length > 0 && (
        <>
          <MetricBannerGrid
            groups={CS_GROUPS}
            bannerData={cs.banners}
            selectedMetric={csMetric}
            onMetricClick={setCsMetric}
          />
          {cs.graph.length > 0 && (
            <MetricTrendChart
              graphData={cs.graph}
              metric={csMetric}
              onMetricChange={setCsMetric}
              groups={CS_GROUPS}
            />
          )}
        </>
      )}

      {/* 无数据提示 */}
      {!loading && accountId && !hasData && (
        <div className="flex flex-col items-center justify-center py-16 text-gray-400">
          <p>暂无数据</p>
          <p className="text-sm mt-1">请确认账号Cookie有效且已开通卖家数据罗盘</p>
        </div>
      )}

      {/* 未选择账号提示 */}
      {!accountId && (
        <div className="flex flex-col items-center justify-center py-16 text-gray-400">
          <p>请先选择一个账号</p>
        </div>
      )}
    </div>
  )
}
