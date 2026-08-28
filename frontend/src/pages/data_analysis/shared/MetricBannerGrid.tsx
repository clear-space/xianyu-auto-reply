/**
 * 分组指标卡片墙（数据罗盘通用）
 *
 * 渲染 graphBannerBenchData.bannerDataList：按分组展示指标卡，
 * 支持复合卡（extendInfo 子指标）、竞争力角标、涨跌幅与上周期对比。
 * 点击卡片可选为当前趋势图指标。
 */
import { motion } from 'framer-motion'
import { TrendingDown, TrendingUp, Trophy } from 'lucide-react'
import type { BannerDataItem } from '@/api/data_analysis'
import {
  COMPOSITE_LABELS,
  formatBannerValue,
  type MetricDef,
  type MetricGroupDef,
} from '../metrics'

interface MetricBannerGridProps {
  groups: MetricGroupDef[]
  bannerData: BannerDataItem[]
  /** 当前选中的趋势图指标 */
  selectedMetric?: string
  onMetricClick?: (name: string) => void
  /** 卡片点击是否有选中态（无趋势图的页面可关闭） */
  selectable?: boolean
}

/** 单个指标卡 */
function MetricCard({
  item,
  def,
  selected,
  onClick,
}: {
  item: BannerDataItem | undefined
  def: MetricDef
  selected?: boolean
  onClick?: () => void
}) {
  const value = formatBannerValue(item)
  const ratio = item?.ratio
  const isUp = ratio != null && ratio > 0
  const isDown = ratio != null && ratio < 0

  return (
    <div
      className={`bg-white dark:bg-slate-800 rounded-lg p-3 shadow-sm border transition-all ${
        selected
          ? 'border-blue-500 ring-2 ring-blue-200 dark:ring-blue-800 shadow-md'
          : 'border-gray-100 dark:border-slate-700 hover:shadow-md hover:border-blue-300 dark:hover:border-blue-600'
      } ${onClick ? 'cursor-pointer' : ''}`}
      onClick={onClick}
    >
      <div className="flex items-center justify-between mb-1.5">
        <span
          className={`text-xs flex items-center gap-1 truncate ${
            selected ? 'text-blue-600 dark:text-blue-400 font-medium' : 'text-gray-500 dark:text-gray-400'
          }`}
          title={def.uncertain ? `${def.label}（名称按字段语义推断）` : def.label}
        >
          {def.label}
          {def.uncertain && <span className="text-gray-300 dark:text-gray-600">?</span>}
        </span>
        {def.competitive && item && item.dataStr && item.dataStr !== '-' && !isNaN(parseFloat(item.dataStr)) && (
          <span className="flex items-center gap-0.5 text-[10px] px-1 py-0.5 rounded bg-amber-50 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400">
            <Trophy className="w-2.5 h-2.5" />
            超越{parseFloat(item.dataStr).toFixed(0)}%同行
          </span>
        )}
      </div>
      <div className="flex items-end justify-between">
        <span className="text-xl font-bold text-gray-800 dark:text-gray-100">
          {def.format === 'currency' && value !== '--' ? `¥${value}` : value}
        </span>
        <div className="text-right min-w-[52px]">
          {item?.ratioFormat && item.ratioFormat !== '-' ? (
            <span
              className={`text-xs flex items-center gap-0.5 justify-end ${
                isUp ? 'text-green-500' : isDown ? 'text-red-500' : 'text-gray-400'
              }`}
            >
              {isUp && <TrendingUp className="w-3 h-3" />}
              {isDown && <TrendingDown className="w-3 h-3" />}
              {isUp ? '+' : ''}{item.ratioFormat}
            </span>
          ) : (
            <span className="text-xs text-gray-400">--</span>
          )}
          {item?.lastDataStr && item.lastDataStr !== '-' && (
            <div className="text-xs text-gray-400 mt-0.5">
              前{item.cycle?.replace('前', '') || ''} {item.lastDataStr}
            </div>
          )}
        </div>
      </div>
      {/* 复合卡：extendInfo 子指标 */}
      {def.composite && item?.extendInfo && (
        <div className="mt-1.5 pt-1.5 border-t border-gray-100 dark:border-slate-700 flex gap-3">
          {def.composite.map((sub) => {
            const v = item.extendInfo?.[sub]
            return (
              <span key={sub} className="text-xs text-gray-500 dark:text-gray-400">
                {COMPOSITE_LABELS[sub] || sub}
                <span className="ml-1 font-medium text-gray-700 dark:text-gray-300">
                  {v == null ? '--' : v}
                </span>
              </span>
            )
          })}
        </div>
      )}
    </div>
  )
}

export function MetricBannerGrid({
  groups,
  bannerData,
  selectedMetric,
  onMetricClick,
  selectable = true,
}: MetricBannerGridProps) {
  const getItem = (name: string) => bannerData.find((b) => b.name === name)

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-4"
    >
      {groups.map((group) => {
        const items = group.metrics
          .map((m) => ({ def: m, item: getItem(m.name) }))
          .filter(({ item }) => item)
        if (items.length === 0) return null
        return (
          <div key={group.key}>
            <h3 className="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2 flex items-center gap-1.5">
              <span className="w-1 h-3.5 rounded bg-blue-500 inline-block" />
              {group.label}
            </h3>
            <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-3">
              {items.map(({ def, item }) => (
                <MetricCard
                  key={def.name}
                  item={item}
                  def={def}
                  selected={selectable && selectedMetric === def.name}
                  onClick={selectable && onMetricClick ? () => onMetricClick(def.name) : undefined}
                />
              ))}
            </div>
          </div>
        )
      })}
    </motion.div>
  )
}
