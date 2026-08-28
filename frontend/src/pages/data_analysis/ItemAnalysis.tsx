/**
 * 商品分析页面
 *
 * - 顶部：商品维度概览（item.summary 14 个指标）
 * - 中部：商品列表（item.list 分页，含曝光/浏览/咨询/成交/退款）
 * - 抽屉：单品指标详情（item.indicators 提供字段 label 表，数值取自列表行）
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import {
  getItemIndicators,
  getItemList,
  getItemSummary,
  type IndicatorGroupDef,
  type ItemListResponse,
  type ItemListRow,
  type OverviewModuleResponse,
} from '@/api/data_analysis'
import { useUIStore } from '@/store/uiStore'
import { ITEM_SUMMARY_GROUPS } from './metrics'
import { AccountTimeRangeBar } from './shared/AccountTimeRangeBar'
import { MetricBannerGrid } from './shared/MetricBannerGrid'
import { useDataQuery } from './shared/useDataQuery'

const PAGE_SIZE = 20

/** 单品指标抽屉 */
function ItemIndicatorDrawer({
  row,
  indicatorGroups,
  onClose,
}: {
  row: ItemListRow
  indicatorGroups: IndicatorGroupDef[]
  onClose: () => void
}) {
  const val = (name: string): string => {
    const v = row[name]
    if (v == null) return '--'
    return String(v)
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onClick={onClose}>
      <div
        className="w-full max-w-md h-full bg-white dark:bg-slate-800 shadow-xl overflow-y-auto p-4 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-gray-800 dark:text-gray-200">单品指标</h3>
          <button
            className="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-slate-700 text-gray-500"
            onClick={onClose}
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex items-center gap-3">
          <img
            src={row.itmPicUrl}
            alt=""
            className="w-14 h-14 rounded object-cover bg-gray-100 dark:bg-slate-700"
            onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden' }}
          />
          <div className="min-w-0">
            <p className="text-sm text-gray-800 dark:text-gray-200 line-clamp-2" title={row.itmName}>
              {row.itmName}
            </p>
            <p className="text-xs text-gray-400 mt-0.5">
              价格 ¥{row.itmPrice} · 在架 {row.daysOnShelf} 天 · {row.postDt}
            </p>
          </div>
        </div>
        {indicatorGroups.map((group) => (
          <div key={group.name}>
            <h4 className="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2">{group.title}</h4>
            <div className="grid grid-cols-2 gap-2">
              {group.subFields.map((field) => (
                <div
                  key={field.name}
                  className="bg-gray-50 dark:bg-slate-700/50 rounded-lg p-2.5"
                >
                  <p className="text-xs text-gray-500 dark:text-gray-400">{field.title}</p>
                  <p className="text-base font-semibold text-gray-800 dark:text-gray-100 mt-0.5">
                    {val(field.name)}
                  </p>
                </div>
              ))}
            </div>
          </div>
        ))}
        {indicatorGroups.length === 0 && (
          <p className="text-xs text-gray-400 text-center py-8">指标定义加载中...</p>
        )}
      </div>
    </div>
  )
}

export function ItemAnalysis() {
  const { addToast } = useUIStore()
  const [summaryBanners, setSummaryBanners] = useState<NonNullable<OverviewModuleResponse['data']>['data']['graphBannerBenchData']['bannerDataList']>([])
  const [list, setList] = useState<ItemListRow[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [selectedRow, setSelectedRow] = useState<ItemListRow | null>(null)
  const [indicatorGroups, setIndicatorGroups] = useState<IndicatorGroupDef[]>([])
  const listRef = useRef<HTMLDivElement>(null)

  const onResult = useCallback(
    (result: ItemListResponse | null) => {
      if (result?.success && result.data) {
        setList(result.data.data.list || [])
        setTotal(result.data.data.total || 0)
      } else {
        if (result && !result.success) {
          addToast({ type: 'error', message: result.message || '获取商品列表失败' })
        }
        setList([])
        setTotal(0)
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
    buildParams,
  } = useDataQuery<ItemListResponse>({
    fetcher: (params) => getItemList({ ...params, page_num: 1, page_size: PAGE_SIZE }),
    onResult,
    autoFetch: false, // 翻页与汇总接口分开控制，手动触发
  })

  /** 翻页查询（固定第一页之外的页码） */
  const fetchPage = useCallback(
    async (pageNum: number) => {
      const { params } = buildParams()
      if (!params) return
      try {
        const r = await getItemList({
          ...params,
          page_num: pageNum,
          page_size: PAGE_SIZE,
        })
        if (r.success && r.data) {
          setList(r.data.data.list || [])
          setTotal(r.data.data.total || 0)
        } else {
          addToast({ type: 'error', message: r.message || '获取商品列表失败' })
        }
      } catch {
        addToast({ type: 'error', message: '获取商品列表失败，请稍后重试' })
      }
    },
    [buildParams, addToast],
  )

  /** 账号/时间变化时重置到第一页 */
  useEffect(() => {
    setPage(1)
  }, [accountId, dateType, customStart, customEnd])

  /** 顶部商品概览 + 指标定义表（随账号/时间变化拉取） */
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      const { params } = buildParams()
      if (!params) {
        setSummaryBanners([])
        return
      }
      const [summary, indicators] = await Promise.allSettled([
        getItemSummary(params),
        getItemIndicators({
          account_id: params.account_id,
          item_id: '',
          date_type: params.date_type,
          date_range: params.date_range,
        }),
      ])
      if (cancelled) return
      if (summary.status === 'fulfilled' && summary.value.success && summary.value.data) {
        setSummaryBanners(summary.value.data.data.graphBannerBenchData?.bannerDataList || [])
      } else {
        setSummaryBanners([])
      }
      if (indicators.status === 'fulfilled' && indicators.value.success && indicators.value.data) {
        setIndicatorGroups(indicators.value.data.data || [])
      } else {
        setIndicatorGroups([])
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [buildParams, accountId, dateType, customStart, customEnd])

  /** 首次进入（账号/时间就绪）时拉第一页 */
  useEffect(() => {
    if (accountId && dateType !== 'customDate') {
      query()
    }
  }, [accountId, dateType, query])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  /** 打开单品详情：指标定义未加载时先拉取 */
  const openDetail = async (row: ItemListRow) => {
    setSelectedRow(row)
    if (indicatorGroups.length === 0 && accountId) {
      try {
        const r = await getItemIndicators({ account_id: accountId, item_id: '' })
        if (r.success && r.data) setIndicatorGroups(r.data.data || [])
      } catch {
        /* 静默失败，抽屉内显示加载中 */
      }
    }
  }

  return (
    <div className="space-y-4">
      <AccountTimeRangeBar
        title="商品分析"
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
        onQuery={() => {
          setPage(1)
          query()
        }}
      />

      {/* 商品概览 banner */}
      {!loading && summaryBanners.length > 0 && (
        <MetricBannerGrid groups={ITEM_SUMMARY_GROUPS} bannerData={summaryBanners} selectable={false} />
      )}

      {/* 商品列表 */}
      <div ref={listRef} className="bg-white dark:bg-slate-800 rounded-lg shadow-sm border border-gray-100 dark:border-slate-700 overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 dark:border-slate-700">
          <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">
            商品明细
            {total > 0 && <span className="ml-2 text-xs text-gray-400">共 {total} 件</span>}
          </h3>
          {totalPages > 1 && (
            <div className="flex items-center gap-2 text-sm">
              <button
                className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 disabled:opacity-50 text-gray-600 dark:text-gray-300"
                disabled={page <= 1 || loading}
                onClick={() => { const p = page - 1; setPage(p); fetchPage(p); listRef.current?.scrollIntoView({ behavior: 'smooth' }) }}
              >
                上一页
              </button>
              <span className="text-xs text-gray-500 dark:text-gray-400">
                {page} / {totalPages}
              </span>
              <button
                className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 disabled:opacity-50 text-gray-600 dark:text-gray-300"
                disabled={page >= totalPages || loading}
                onClick={() => { const p = page + 1; setPage(p); fetchPage(p) }}
              >
                下一页
              </button>
            </div>
          )}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 dark:text-gray-400 border-b border-gray-100 dark:border-slate-700">
                <th className="px-4 py-2.5 font-medium">商品</th>
                <th className="px-3 py-2.5 font-medium">类目</th>
                <th className="px-3 py-2.5 font-medium text-right">在架天数</th>
                <th className="px-3 py-2.5 font-medium text-right">曝光</th>
                <th className="px-3 py-2.5 font-medium text-right">浏览</th>
                <th className="px-3 py-2.5 font-medium text-right">咨询</th>
                <th className="px-3 py-2.5 font-medium text-right">支付金额</th>
                <th className="px-3 py-2.5 font-medium text-right">支付订单</th>
                <th className="px-3 py-2.5 font-medium text-right">转化率</th>
                <th className="px-3 py-2.5 font-medium text-right">退款订单</th>
                <th className="px-3 py-2.5 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {list.map((row) => (
                <tr
                  key={row.itmId}
                  className="border-b border-gray-50 dark:border-slate-700/50 hover:bg-gray-50 dark:hover:bg-slate-700/40 cursor-pointer"
                  onClick={() => openDetail(row)}
                >
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2.5 min-w-[220px] max-w-[320px]">
                      <img
                        src={row.itmPicUrl}
                        alt=""
                        className="w-10 h-10 rounded object-cover bg-gray-100 dark:bg-slate-700 flex-shrink-0"
                        onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden' }}
                      />
                      <div className="min-w-0">
                        <p className="text-gray-800 dark:text-gray-200 truncate" title={row.itmName}>
                          {row.itmName}
                        </p>
                        <p className="text-xs text-gray-400">¥{row.itmPrice}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">
                    {row.chnlCateLevel2Name || row.chnlCateLevel1Name || '--'}
                  </td>
                  <td className="px-3 py-2.5 text-right text-gray-700 dark:text-gray-300">{row.daysOnShelf}</td>
                  <td className="px-3 py-2.5 text-right text-gray-700 dark:text-gray-300">{row.showPv}</td>
                  <td className="px-3 py-2.5 text-right text-gray-700 dark:text-gray-300">{row.ipv}</td>
                  <td className="px-3 py-2.5 text-right text-gray-700 dark:text-gray-300">{row.chatUv}</td>
                  <td className="px-3 py-2.5 text-right text-gray-700 dark:text-gray-300">¥{row.payAmt}</td>
                  <td className="px-3 py-2.5 text-right text-gray-700 dark:text-gray-300">{row.payOrdCnt}</td>
                  <td className="px-3 py-2.5 text-right text-gray-700 dark:text-gray-300">{row.ipvPayUcvr}</td>
                  <td className="px-3 py-2.5 text-right text-gray-700 dark:text-gray-300">{row.crtRfdOrdCnt}</td>
                  <td className="px-3 py-2.5">
                    <span className="text-xs text-blue-500 hover:text-blue-600">详情</span>
                  </td>
                </tr>
              ))}
              {list.length === 0 && !loading && (
                <tr>
                  <td colSpan={11} className="px-4 py-10 text-center text-gray-400">
                    暂无商品数据，请先选择账号并查询
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 单品指标抽屉 */}
      {selectedRow && (
        <ItemIndicatorDrawer
          row={selectedRow}
          indicatorGroups={indicatorGroups}
          onClose={() => setSelectedRow(null)}
        />
      )}
    </div>
  )
}
