/**
 * 数据总览页面
 *
 * 展示卖家数据概览（官方 36 个指标分 5 组 + 全字段趋势图）：
 * - 成交数据 / 流量数据 / 复购数据 / 商品运营 / 同行竞争力
 * - 趋势图支持全部图表字段切换
 * - 支付金额复合卡（首次/复购拆分）、竞争力角标、数据区间展示
 * - 无数据指标（官方返回 '-'）显示为 --
 */
import { Component, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowRight, BarChart3, Users } from 'lucide-react'
import {
  getFlowDetail,
  getSellerSummary,
  type BannerDataItem,
  type FlowTransferData,
  type GraphDataItem,
  type SellerSummaryResponse,
} from '@/api/data_analysis'
import { useUIStore } from '@/store/uiStore'
import { CHART_METRIC_GROUPS, formatBannerValue, OVERVIEW_GROUPS } from './metrics'
import { AccountTimeRangeBar } from './shared/AccountTimeRangeBar'
import { MetricBannerGrid } from './shared/MetricBannerGrid'
import { MetricTrendChart, formatGraphDate } from './shared/MetricTrendChart'
import { toCompactDate, useDataQuery } from './shared/useDataQuery'

/** 格式化区间日期（20260821 -> 08-21） */
function formatRangeDate(ds: string): string {
  return formatGraphDate(ds).replace('/', '-')
}

/** 漏斗单环节 */
function FunnelStage({
  title,
  main,
  subLabel,
  sub,
}: {
  title: string
  main?: BannerDataItem
  subLabel: string
  sub?: BannerDataItem
}) {
  if (!main) return null
  const ratio = main.ratio
  const isUp = ratio != null && ratio > 0
  const isDown = ratio != null && ratio < 0
  return (
    <div className="flex-1 min-w-[150px] bg-gray-50 dark:bg-slate-700/50 rounded-lg p-3 text-center">
      <p className="text-xs text-gray-500 dark:text-gray-400">{title}</p>
      <p className="text-xl font-bold text-gray-800 dark:text-gray-100">
        {formatBannerValue(main)}
      </p>
      <p className="text-xs text-gray-400">
        {subLabel} {sub ? formatBannerValue(sub) : '--'}
      </p>
      {main.ratioFormat && main.ratioFormat !== '-' && (
        <p
          className={`text-xs mt-0.5 ${
            isUp ? 'text-green-500' : isDown ? 'text-red-500' : 'text-gray-400'
          }`}
        >
          {isUp ? '↑' : isDown ? '↓' : ''} {main.ratioFormat}
        </p>
      )}
    </div>
  )
}

/** 漏斗箭头（含环节间转化率） */
function FunnelArrow({ rate }: { rate?: BannerDataItem }) {
  return (
    <div className="flex flex-col items-center px-1">
      <ArrowRight className="w-4 h-4 text-gray-300 dark:text-gray-600" />
      <span className="text-xs text-blue-500 mt-0.5">
        {rate ? formatBannerValue(rate) : '--'}
      </span>
    </div>
  )
}

/** 流量转化漏斗横幅（浏览 → 咨询 → 支付）
 *  注意：flow.detail 的每个指标是 BannerDataItem 结构，展示用 dataStr */
function FlowFunnel({ data }: { data: FlowTransferData | null }) {
  if (!data || !data.ipvUv || !data.chatUv || !data.payUv) return null
  return (
    <div className="bg-white dark:bg-slate-800 rounded-lg p-4 shadow-sm border border-gray-100 dark:border-slate-700">
      <div className="flex items-center mb-3">
        <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">流量转化漏斗</h3>
        <span className="ml-3 text-xs text-gray-400">
          浏览支付转化率 {formatBannerValue(data.ipvPayUcvr)}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <FunnelStage title="浏览" main={data.ipvUv} subLabel="人次" sub={data.ipv} />
        <FunnelArrow rate={data.ipvChatUcvr} />
        <FunnelStage title="咨询" main={data.chatUv} subLabel="人次" sub={data.chatCnt} />
        <FunnelArrow rate={data.chatPayUcvr} />
        <FunnelStage title="支付" main={data.payUv} subLabel="金额 ¥" sub={data.payAmt} />
      </div>
    </div>
  )
}

/** 漏斗局部错误边界：数据形态异常时静默隐藏，不影响整页 */
class FunnelErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  state = { hasError: false }
  static getDerivedStateFromError() {
    return { hasError: true }
  }
  render() {
    return this.state.hasError ? null : this.props.children
  }
}

export function DataOverview() {
  const { addToast } = useUIStore()
  const [bannerData, setBannerData] = useState<BannerDataItem[]>([])
  const [graphData, setGraphData] = useState<GraphDataItem[]>([])
  const [realDateRange, setRealDateRange] = useState<string[]>([])
  const [chartMetric, setChartMetric] = useState('payAmt')
  const [flowData, setFlowData] = useState<FlowTransferData | null>(null)
  const chartRef = useRef<HTMLDivElement>(null)

  const onResult = useCallback(
    (result: SellerSummaryResponse | null) => {
      if (result?.success && result.data) {
        const summaryData = result.data.data?.graphBannerBenchData
        setBannerData(summaryData?.bannerDataList || [])
        setGraphData(summaryData?.graphDataList || [])
        setRealDateRange(result.data.extendInfo?.realDateRange || [])
      } else {
        if (result && !result.success) {
          addToast({ type: 'error', message: result.message || '获取数据失败' })
        }
        setBannerData([])
        setGraphData([])
        setRealDateRange([])
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
  } = useDataQuery<SellerSummaryResponse>({
    fetcher: (params) => getSellerSummary(params),
    onResult,
  })

  /** 点击指标卡切换到对应趋势图并滚动到图表 */
  const handleMetricClick = useCallback((name: string) => {
    setChartMetric(name)
    chartRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [])

  /** 转化漏斗：跟随当前账号/时间范围拉取（失败静默隐藏） */
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      if (!accountId) {
        setFlowData(null)
        return
      }
      let dateRange = ''
      if (dateType === 'customDate') {
        if (!customStart || !customEnd || customStart > customEnd) {
          setFlowData(null)
          return
        }
        dateRange = `${toCompactDate(customStart)}|${toCompactDate(customEnd)}`
      }
      try {
        const r = await getFlowDetail({
          account_id: accountId,
          date_type: dateType,
          date_range: dateRange,
        })
        if (!cancelled) {
          setFlowData(r.success && r.data ? r.data.data.itemFlowTransferData : null)
        }
      } catch {
        if (!cancelled) setFlowData(null)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [accountId, dateType, customStart, customEnd])

  const rangeLabel =
    realDateRange.length === 2
      ? `${formatRangeDate(realDateRange[0])} ~ ${formatRangeDate(realDateRange[1])}`
      : null

  return (
    <div className="space-y-4">
      <AccountTimeRangeBar
        title="数据总览"
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
            <span className="text-xs text-gray-400 dark:text-gray-500">
              数据区间 {rangeLabel}
            </span>
          ) : undefined
        }
      />

      {/* 加载遮罩 */}
      {loading && (
        <div className="flex items-center justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
          <span className="ml-3 text-gray-500 dark:text-gray-400">加载中...</span>
        </div>
      )}

      {/* 流量转化漏斗横幅 */}
      {!loading && flowData && (
        <FunnelErrorBoundary>
          <FlowFunnel data={flowData} />
        </FunnelErrorBoundary>
      )}

      {/* 分组指标卡片墙 */}
      {!loading && bannerData.length > 0 && (
        <MetricBannerGrid
          groups={OVERVIEW_GROUPS}
          bannerData={bannerData}
          selectedMetric={chartMetric}
          onMetricClick={handleMetricClick}
        />
      )}

      {/* 趋势图（全字段可选） */}
      {!loading && graphData.length > 0 && (
        <div ref={chartRef} className="scroll-mt-4">
          <MetricTrendChart
            graphData={graphData}
            metric={chartMetric}
            onMetricChange={setChartMetric}
            groups={CHART_METRIC_GROUPS}
          />
        </div>
      )}

      {/* 无数据提示 */}
      {!loading && bannerData.length === 0 && accountId && (
        <div className="flex flex-col items-center justify-center py-16 text-gray-400">
          <BarChart3 className="w-12 h-12 mb-3" />
          <p>暂无数据</p>
          <p className="text-sm mt-1">请确认账号Cookie有效且已开通卖家数据罗盘</p>
        </div>
      )}

      {/* 未选择账号提示 */}
      {!accountId && (
        <div className="flex flex-col items-center justify-center py-16 text-gray-400">
          <Users className="w-12 h-12 mb-3" />
          <p>请先选择一个账号</p>
        </div>
      )}
    </div>
  )
}
