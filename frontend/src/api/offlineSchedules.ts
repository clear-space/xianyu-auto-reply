/**
 * 自动下架规则 API
 *
 * 功能：
 * 1. 下架规则 CRUD（每天/每周，复用定时发布的时间配置）
 * 2. 手动触发
 * 3. 执行历史查询与清空（支持保留天数）
 */
import { get, post, put, del, patch } from '@/utils/request'
import type { ApiResponse } from '@/types'
import type { ScheduleConfig } from './productPublish'

const PREFIX = '/api/v1/product-publish/offline-schedules'

/** 下架规则 */
export interface OfflineSchedule {
  id: number
  user_id: number
  name: string
  schedule_mode: 'daily' | 'weekly'
  schedule_config: ScheduleConfig
  account_ids: string[]
  max_count: number
  delist_algorithm_id?: number | null
  delist_algorithm_name?: string | null
  enabled: boolean
  last_triggered_at?: string | null
  next_trigger_at?: string | null
  account_count?: number
  created_at: string
  updated_at: string
}

/** 创建下架规则参数 */
export interface CreateOfflineScheduleParams {
  name: string
  schedule_mode: 'daily' | 'weekly'
  schedule_config: ScheduleConfig
  account_ids: string[]
  max_count: number
  delist_algorithm_id?: number | null
}

/** 更新下架规则参数 */
export interface UpdateOfflineScheduleParams {
  name?: string
  schedule_mode?: string
  schedule_config?: ScheduleConfig
  account_ids?: string[]
  max_count?: number
  delist_algorithm_id?: number | null
  enabled?: boolean
}

/** 下架执行记录 */
export interface OfflineScheduleLog {
  id: number
  schedule_id: number
  schedule_name?: string | null
  batch_id?: string | null
  scheduled_at: string
  executed_at?: string | null
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  total_count: number
  success_count: number
  failed_count: number
  error_message?: string | null
  detail_json?: OfflineLogDetail | null
  created_at: string
}

/** 下架执行明细（detail_json） */
export interface OfflineLogDetail {
  max_count?: number
  sample_mode?: string
  algorithm_id?: number | null
  algorithm_name?: string
  detail_truncated?: boolean
  accounts?: Array<{
    account_id: string
    status: 'success' | 'partial' | 'failed' | 'account_error'
    suc_count: number
    fail_count: number
    error?: string
    items?: Array<{ item_id: string; title?: string; item_no?: string | null; weight?: number; result: string; error?: string; note?: string }>
  }>
  missing_accounts?: string[]
}

/** 分页响应 */
export interface OfflineListResponse {
  success: boolean
  message: string
  data: {
    list: OfflineSchedule[]
    total: number
    page: number
    page_size: number
    total_pages: number
  }
}

export interface OfflineLogListResponse {
  success: boolean
  message: string
  data: {
    list: OfflineScheduleLog[]
    total: number
    page: number
    page_size: number
    total_pages: number
  }
}

/** 创建下架规则 */
export const createOfflineSchedule = (params: CreateOfflineScheduleParams): Promise<ApiResponse> =>
  post(PREFIX, params)

/** 分页查询下架规则 */
export const getOfflineSchedules = (page = 1, pageSize = 20): Promise<OfflineListResponse> => {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  return get(`${PREFIX}?${params}`)
}

/** 更新下架规则 */
export const updateOfflineSchedule = (id: number, params: UpdateOfflineScheduleParams): Promise<ApiResponse> =>
  put(`${PREFIX}/${id}`, params)

/** 删除下架规则 */
export const deleteOfflineSchedule = (id: number): Promise<ApiResponse> =>
  del(`${PREFIX}/${id}`)

/** 切换启用/禁用 */
export const toggleOfflineSchedule = (id: number): Promise<ApiResponse> =>
  patch(`${PREFIX}/${id}/toggle`)

/** 手动触发一次下架 */
export const triggerOfflineSchedule = (id: number): Promise<ApiResponse> =>
  post(`${PREFIX}/${id}/trigger`)

/** 分页查询所有下架执行历史 */
export const getAllOfflineScheduleLogs = (page = 1, pageSize = 20): Promise<OfflineLogListResponse> => {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  return get(`${PREFIX}/logs/global?${params}`)
}

/** 清空下架执行日志（days>0 只清 N 天前的，不传或 0 清空全部） */
export const clearOfflineScheduleLogs = async (days?: number): Promise<{ success: boolean; message: string }> => {
  const qs = days && days > 0 ? `?days=${days}` : ''
  return post<{ success: boolean; message: string }>(`${PREFIX}/logs/clear${qs}`)
}
