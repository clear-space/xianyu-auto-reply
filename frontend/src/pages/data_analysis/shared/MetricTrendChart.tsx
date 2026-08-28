/**
 * 指标趋势图（数据罗盘通用）
 *
 * 渲染 graphBannerBenchData.graphDataList：指标下拉（按分组）+ 折线趋势。
 * Y 轴按指标 format 类型换算（percent 类官方返回小数）。
 */
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { BarChart3 } from 'lucide-react'
import type { GraphDataItem } from '@/api/data_analysis'
import { formatChartValue, type MetricGroupDef } from '../metrics'

interface MetricTrendChartProps {
  graphData: GraphDataItem[]
  metric: string
  onMetricChange: (name: string) => void
  /** 指标分组（下拉用） */
  groups: MetricGroupDef[]
  height?: number
}

/** 格式化日期（20260527 -> 05/27） */
export function formatGraphDate(ds: string): string {
  if (!ds || ds.length !== 8) return ds
  return `${ds.slice(4, 6)}/${ds.slice(6, 8)}`
}

export function MetricTrendChart({
  graphData,
  metric,
  onMetricChange,
  groups,
  height = 400,
}: MetricTrendChartProps) {
  const allMetrics = groups.flatMap((g) => g.metrics)
  const current = allMetrics.find((m) => m.name === metric)

  return (
    <div className="bg-white dark:bg-slate-800 rounded-lg p-4 shadow-sm border border-gray-100 dark:border-slate-700">
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
          <BarChart3 className="w-4 h-4" />
          {current?.label || metric} - 趋势图
        </h3>
        <select
          className="ml-auto px-2 py-1 text-sm border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-slate-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={metric}
          onChange={(e) => onMetricChange(e.target.value)}
        >
          {groups.map((g) => (
            <optgroup key={g.key} label={g.label}>
              {g.metrics.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.label}{m.uncertain ? '（推测）' : ''}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={graphData.map((item) => ({ ...item, date: formatGraphDate(item.ds) }))}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
            <XAxis dataKey="date" tick={{ fontSize: 12 }} stroke="#9ca3af" />
            <YAxis
              tick={{ fontSize: 12 }}
              stroke="#9ca3af"
              tickFormatter={(v: number) => formatChartValue(v, current)}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: 'rgba(255,255,255,0.95)',
                border: '1px solid #e5e7eb',
                borderRadius: '8px',
                fontSize: '12px',
              }}
              labelFormatter={(label) => `日期: ${label}`}
              formatter={(value) => [
                formatChartValue(Number(value), current),
                current?.label || metric,
              ]}
            />
            <Legend />
            <Line
              type="monotone"
              dataKey={metric}
              name={current?.label || metric}
              stroke="#3b82f6"
              strokeWidth={2}
              dot={{ r: 3 }}
              activeDot={{ r: 5 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
