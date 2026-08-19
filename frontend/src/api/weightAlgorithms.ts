/**
 * 上架权重算法 API（管理员）
 *
 * 管理员集中定义权重调参规则，定时发布规则引用算法ID来获得素材权重。
 */
import { get, post, put, del, patch } from '@/utils/request'
import type { ApiResponse } from '@/types'

const PREFIX = '/api/v1/admin/weight-algorithms'

/** 权重参数 */
export interface WeightAlgorithmParams {
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

/** 权重算法 */
export interface WeightAlgorithm {
  id: number
  name: string
  algorithm_type: string
  description?: string | null
  params: WeightAlgorithmParams
  enabled: boolean
  is_builtin: boolean
  ref_count: number
  created_at?: string
  updated_at?: string
}

/** 算法列表（含系统默认参数） */
export const getWeightAlgorithms = (): Promise<
  ApiResponse<{ list: WeightAlgorithm[]; default_params: WeightAlgorithmParams }>
> => get(PREFIX)

/** 算法保存载荷 */
export interface WeightAlgorithmPayload {
  name?: string
  algorithm_type?: string
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

/** 算法效果预览条目（权重 + 信号明细 + 逐项分值） */
export interface WeightPreviewEntry {
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

/** 预览算法效果（当前用户最近素材的权重分布） */
export const getWeightAlgorithmPreview = (id: number): Promise<
  ApiResponse<{ algorithm: WeightAlgorithm; total: number; list: WeightPreviewEntry[] }>
> => get(`${PREFIX}/${id}/preview`)

/** 引用该算法的定时发布规则摘要 */
export interface WeightAlgorithmReference {
  id: number
  name: string
  user_id: number
  publish_mode: 'specified' | 'random'
  random_count?: number | null
  enabled: boolean
  next_trigger_at?: string | null
}

/** 查看引用该算法的定时发布规则列表 */
export const getWeightAlgorithmReferences = (id: number): Promise<
  ApiResponse<{ list: WeightAlgorithmReference[] }>
> => get(`${PREFIX}/${id}/references`)
