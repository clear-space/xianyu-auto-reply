/**
 * 数据罗盘通用查询 Hook
 *
 * 封装各数据页面的公共逻辑：账号列表加载、时间范围状态、自定义日期校验、
 * 请求参数组装、非自定义日期自动拉取、错误提示。
 * 数据总览 / 流量分布 / 经营数据 / 商品分析 均复用。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { getAccountDetails } from '@/api/accounts'
import { useUIStore } from '@/store/uiStore'
import type { AccountDetail } from '@/types'

/** 时间范围选项 */
export const DATE_TYPE_OPTIONS = [
  { value: 'recent1d', label: '近1天' },
  { value: 'recent7d', label: '近7天' },
  { value: 'recent30d', label: '近30天' },
  { value: 'customDate', label: '自定义' },
] as const

export type DateTypeValue = (typeof DATE_TYPE_OPTIONS)[number]['value']

/** 各页面 fetcher 的入参 */
export interface DataQueryParams {
  account_id: number
  date_type: DateTypeValue
  date_range: string
}

interface UseDataQueryOptions<T> {
  fetcher: (params: DataQueryParams) => Promise<T>
  /** 手动查询（自定义日期时）与自动查询的公共回调，成功时处理响应 */
  onResult: (result: T) => void
  /** 是否自动拉取（账号/时间范围变化时） */
  autoFetch?: boolean
  /** 附加重拉触发键（如当前 Tab）：变化时按当前参数重新拉取 */
  refreshKey?: string | number
}

/** 将 yyyy-MM-dd 转为 yyyyMMdd */
export function toCompactDate(dateStr: string): string {
  return dateStr.replace(/-/g, '')
}

export function useDataQuery<T>({ fetcher, onResult, autoFetch = true, refreshKey }: UseDataQueryOptions<T>) {
  const { addToast } = useUIStore()
  const [accounts, setAccounts] = useState<AccountDetail[]>([])
  const [accountId, setAccountId] = useState<number | null>(null)
  const [dateType, setDateType] = useState<DateTypeValue>('recent1d')
  const [customStart, setCustomStart] = useState('')
  const [customEnd, setCustomEnd] = useState('')
  const [loading, setLoading] = useState(false)

  // 加载账号列表
  useEffect(() => {
    const loadAccounts = async () => {
      try {
        const data = await getAccountDetails()
        setAccounts(data)
      } catch {
        addToast({ type: 'error', message: '加载账号列表失败' })
      }
    }
    loadAccounts()
  }, [addToast])

  /** 组装并校验请求参数（静默，不弹提示；提示由 query 负责） */
  const buildParams = useCallback((): { params: DataQueryParams | null; error?: string } => {
    if (!accountId) return { params: null }
    let dateRange = ''
    if (dateType === 'customDate') {
      if (!customStart || !customEnd) {
        return { params: null, error: '请选择开始日期和结束日期' }
      }
      if (customStart > customEnd) {
        return { params: null, error: '开始日期不能晚于结束日期' }
      }
      dateRange = `${toCompactDate(customStart)}|${toCompactDate(customEnd)}`
    }
    return { params: { account_id: accountId, date_type: dateType, date_range: dateRange } }
  }, [accountId, dateType, customStart, customEnd])

  // 用 ref 持有回调：调用方传内联函数时，query 身份不随渲染漂移，
  // 否则自动拉取 effect 会因 query 每渲染都变化而无限重拉。
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  const onResultRef = useRef(onResult)
  onResultRef.current = onResult

  /** 执行查询 */
  const query = useCallback(async () => {
    const { params, error } = buildParams()
    if (!params) {
      if (error) addToast({ type: 'error', message: error })
      return
    }
    setLoading(true)
    try {
      const result = await fetcherRef.current(params)
      onResultRef.current(result)
    } catch {
      addToast({ type: 'error', message: '获取数据失败，请稍后重试' })
      onResultRef.current(null as T)
    } finally {
      setLoading(false)
    }
  }, [buildParams, addToast])

  // 非自定义日期时，账号/时间范围/refreshKey 变化自动拉取
  useEffect(() => {
    if (autoFetch && accountId && dateType !== 'customDate') {
      query()
    }
  }, [autoFetch, accountId, dateType, query, refreshKey])

  return {
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
  }
}
