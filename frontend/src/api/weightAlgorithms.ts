/**
 * 权重算法 API（管理员）
 *
 * 管理员集中定义权重调参规则，规则引用算法ID来获得权重：
 * - 上架热度加权（heat_weight）：定时发布随机模式选料
 * - 下架加权（delist_weight）：定时下架选品排序
 */
import { get, post, put, del, patch } from '@/utils/request'
import type { ApiResponse } from '@/types'

const PREFIX = '/api/v1/admin/weight-algorithms'

export type WeightAlgorithmType = 'heat_weight' | 'delist_weight'

/** 热度加权参数（上架算法） */
export interface HeatWeightParams {
  first_use_bonus: number
  recent_order_bonus: number
  sold_bonus: number
  offline_recover_per_day: number
  deleted_recover_per_day: number
  fail_penalty: number
  exclude_sold: boolean
  /** 选料方式：weighted-加权随机（权重=概率）；top-按权重直选（高分必先选） */
  sample_mode: 'weighted' | 'top'
}

/** 下架加权参数（下架算法，基于闲鱼官方数据归一化加权） */
export interface DelistWeightParams {
  base_score: number
  w_age: number
  w_no_sale: number
  w_exposure: number
  w_browse: number
  w_chat: number
  w_sale: number
  w_ucvr: number
  w_want: number
  w_polished: number
  min_score: number
  /** 归一化方式：percentile-账号内百分位；log-对数归一化 */
  norm_method: 'percentile' | 'log'
  /** 无快照商品：exclude-权重0不参与；base-仅基础分参与 */
  no_data_behavior: 'exclude' | 'base'
  /** 选取方式：top-按权重直选（高分必先下）；weighted-加权随机（权重=概率） */
  sample_mode: 'weighted' | 'top'
  exclude_recent_order: boolean
  exclude_polished: boolean
}

export type WeightAlgorithmParams = HeatWeightParams | DelistWeightParams

/** 权重算法 */
export interface WeightAlgorithm {
  id: number
  name: string
  algorithm_type: WeightAlgorithmType
  description?: string | null
  params: WeightAlgorithmParams
  enabled: boolean
  is_builtin: boolean
  ref_count: number
  created_at?: string
  updated_at?: string
}

/** 算法列表（含两种类型的系统默认参数） */
export const getWeightAlgorithms = (): Promise<
  ApiResponse<{
    list: WeightAlgorithm[]
    default_params: HeatWeightParams
    default_delist_params: DelistWeightParams
  }>
> => get(PREFIX)

/** 算法保存载荷 */
export interface WeightAlgorithmPayload {
  name?: string
  algorithm_type?: WeightAlgorithmType
  description?: string
  params?: WeightAlgorithmParams
  enabled?: boolean
}

/** 新建算法 */
export const createWeightAlgorithm = (payload: WeightAlgorithmPayload & { name: string }): Promise<ApiResponse> =>
  post(PREFIX, payload)

/** 更新算法 */
export const updateWeightAlgorithm = (
  id: number,
  payload: WeightAlgorithmPayload
): Promise<ApiResponse> => put(`${PREFIX}/${id}`, payload)

/** 删除算法（被规则引用时后端会拒绝） */
export const deleteWeightAlgorithm = (id: number): Promise<ApiResponse> =>
  del(`${PREFIX}/${id}`)

/** 切换启用状态 */
export const toggleWeightAlgorithm = (id: number): Promise<ApiResponse> =>
  patch(`${PREFIX}/${id}/toggle`)

/** 热度加权预览条目（素材维度） */
export interface HeatWeightPreviewEntry {
  material_id: number
  title: string
  item_no?: number | null
  weight: number
  /** 权重逐项构成（含零值项，负数为扣分） */
  parts: {
    base: number
    first_use_bonus: number
    recent_order_bonus: number
    sold_bonus: number
    offline_penalty: number
    deleted_penalty: number
    fail_penalty: number
  }
  /** 原始分低于 1 被保底为 1 */
  clamped: boolean
  /** 本地状态在售：执行时去重硬过滤会先排除（实际以规则账号刷新为准） */
  on_sale_filtered?: boolean
  signals: {
    item_status?: string | null
    first_use: boolean
    recent_order: boolean
    sold: boolean
    offline_days?: number | null
    deleted_days?: number | null
    fail_count: number
  }
}

/** 下架加权预览条目（在售商品维度，基于闲鱼官方数据归一化加权） */
export interface DelistWeightPreviewEntry {
  item_id: string
  title: string
  item_no?: number | null
  account_id?: string | null
  weight: number
  /** 权重逐项构成（含零值项，负数为扣分） */
  parts: {
    base: number
    age: number
    no_sale: number
    exposure: number
    browse: number
    chat: number
    sale: number
    ucvr: number
    want: number
    polished: number
  }
  /** 各信号归一化值（0~1，百分位；仅预览透明化展示用） */
  p_values?: {
    age?: number
    no_sale?: number
    exposure?: number
    browse?: number
    chat?: number
    sale?: number
    ucvr?: number
    want?: number
  }
  /** 原始分低于 0 被保底为 0 */
  clamped: boolean
  signals: {
    age_days: number
    no_sale_days: number
    polished: boolean
    show_pv_7d?: number | null
    ipv_7d?: number | null
    chat_uv_7d?: number | null
    pay_ord_cnt_7d?: number | null
    ucvr_7d?: string | null
    want_growth_7d?: number | null
    want_now?: number | null
    no_data?: boolean
    excluded?: boolean
  }
}

export type WeightPreviewEntry = HeatWeightPreviewEntry | DelistWeightPreviewEntry

/** 预览算法效果（热度加权=全部素材；下架加权=在售商品，可指定账号范围、可先同步最新商品） */
export const getWeightAlgorithmPreview = (
  id: number,
  accountIds?: string[],
  refresh?: boolean
): Promise<
  ApiResponse<{ algorithm: WeightAlgorithm; total: number; list: WeightPreviewEntry[] }>
> => {
  const params = new URLSearchParams()
  if (accountIds && accountIds.length > 0) {
    params.append('account_ids', accountIds.join(','))
  }
  if (refresh) {
    params.append('refresh', '1')
  }
  const qs = params.toString()
  // 预览前同步商品需逐页抓取闲鱼列表，商品较多时可能超过默认 90 秒超时
  return get(`${PREFIX}/${id}/preview${qs ? `?${qs}` : ''}`, { timeout: 300000 })
}

/** 引用该算法的规则摘要（上架算法=定时发布规则；下架算法=定时下架规则） */
export interface WeightAlgorithmReference {
  id: number
  name: string
  user_id: number
  publish_mode?: 'specified' | 'random'
  random_count?: number | null
  max_count?: number | null
  enabled: boolean
  next_trigger_at?: string | null
}

/** 查看引用该算法的规则列表 */
export const getWeightAlgorithmReferences = (id: number): Promise<
  ApiResponse<{ list: WeightAlgorithmReference[] }>
> => get(`${PREFIX}/${id}/references`)

/** 启用中的算法选项（规则表单下拉用） */
export interface WeightAlgorithmOption {
  id: number
  name: string
  algorithm_type: WeightAlgorithmType
  description?: string | null
  params: Record<string, unknown>
  is_builtin: boolean
}

/** 算法类型注册表（规则表单两级选择下拉用；新增算法类型时在此登记） */
export const ALGORITHM_TYPES: Array<{ value: WeightAlgorithmType; label: string }> = [
  { value: 'heat_weight', label: '热度加权' },
  { value: 'delist_weight', label: '下架加权' },
]

/** 启用中的算法列表（可按类型过滤） */
export const getWeightAlgorithmOptions = (algorithmType?: WeightAlgorithmType): Promise<
  ApiResponse<{ list: WeightAlgorithmOption[] }>
> => {
  const qs = algorithmType ? `?algorithm_type=${algorithmType}` : ''
  return get(`/api/v1/product-publish/schedules/weight-algorithms${qs}`)
}
