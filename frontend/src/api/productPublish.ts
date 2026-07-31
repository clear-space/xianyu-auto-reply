/**
 * 商品发布 API 接口层
 *
 * 功能：
 * 1. 素材库 CRUD
 * 2. 单品发布 / 批量发布
 * 3. 发布日志查询
 */
import { get, post, put, del, patch } from '@/utils/request'
import type { ApiResponse } from '@/types'

const PREFIX = '/api/v1/product-publish'

// ==================== 类型定义 ====================

export interface ProductMaterial {
  id: number
  user_id: number
  username?: string  // 管理员场景返回
  title: string
  description: string
  price: number
  original_price?: number | null
  category?: string | null
  images: string[]
  delivery_method: 'express' | 'pickup'
  postage: number
  address?: string | null
  brand?: string | null
  condition: string
  stock?: number
  remark?: string | null
  created_at: string
  updated_at: string
}

export interface MaterialCreateParams {
  title: string
  description: string
  price: number
  original_price?: number | null
  category?: string
  images: string[]
  delivery_method?: 'express' | 'pickup'
  postage?: number
  address?: string
  brand?: string
  condition?: string
  stock?: number
  remark?: string
}

export interface MaterialListResponse {
  success: boolean
  message: string
  data: {
    list: ProductMaterial[]
    total: number
    page: number
    page_size: number
    total_pages: number
  }
}

export interface PublishLog {
  id: number
  user_id: number
  username?: string  // 管理员场景返回
  account_id: string
  title: string
  description?: string
  price?: string
  material_id?: number | null
  batch_id?: string | null
  status: 'pending' | 'publishing' | 'success' | 'failed'
  item_url?: string | null
  item_id?: string | null
  error_message?: string | null
  resolved_address_id?: number | null
  resolved_address_text?: string | null
  address_source?: 'material' | 'account_pool' | 'global_pool' | 'personal_pool' | null
  created_at: string
  updated_at: string
}

export interface PublishLogListResponse {
  success: boolean
  message: string
  data: {
    list: PublishLog[]
    total: number
    page: number
    page_size: number
    total_pages: number
  }
}

export interface BatchAccountStatus {
  account_id: string
  total: number
  success: number
  failed: number
  publishing: number
  pending: number
  sync_status: 'pending' | 'running' | 'success' | 'failed' | 'skipped' | 'unknown'
  sync_message: string
  sync_total_count: number
  sync_saved_count: number
}

export interface BatchStatusResponse {
  success: boolean
  message: string
  data: {
    batch_id: string
    total: number
    success: number
    failed: number
    publishing: number
    pending: number
    finished: boolean
    account_statuses: BatchAccountStatus[]
  }
}

export interface PublishSingleResponseData {
  item_url?: string | null
  item_id?: string | null
  log_id?: number
  sync_status?: 'success' | 'failed' | 'skipped'
  sync_message?: string | null
  sync_total_count?: number
  sync_saved_count?: number
}

export type PublishSingleResponse = ApiResponse<PublishSingleResponseData>

export interface PublishBatchResponseData {
  batch_id: string
  total: number
}

export type PublishBatchResponse = ApiResponse<PublishBatchResponseData>

// ==================== 素材库接口 ====================

/** 创建素材 */
export const createMaterial = (params: MaterialCreateParams): Promise<ApiResponse> =>
  post(`${PREFIX}/materials`, params)

/** 分页查询素材列表 */
export const getMaterials = (
  page = 1,
  pageSize = 20,
  filters?: { title?: string; category?: string; condition?: string }
): Promise<MaterialListResponse> => {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  if (filters?.title) params.append('title', filters.title)
  if (filters?.category) params.append('category', filters.category)
  if (filters?.condition) params.append('condition', filters.condition)
  return get(`${PREFIX}/materials?${params}`)
}

/** 获取单条素材详情 */
export const getMaterial = (id: number): Promise<ApiResponse> =>
  get(`${PREFIX}/materials/${id}`)

/** 更新素材 */
export const updateMaterial = (
  id: number,
  params: Partial<MaterialCreateParams>
): Promise<ApiResponse> => put(`${PREFIX}/materials/${id}`, params)

/** 删除素材 */
export const deleteMaterial = (id: number): Promise<ApiResponse> =>
  del(`${PREFIX}/materials/${id}`)

/** 批量删除素材 */
export const batchDeleteMaterials = (ids: number[]): Promise<ApiResponse> =>
  post(`${PREFIX}/materials/batch-delete`, { ids })

// ==================== 发布接口 ====================

/** 单品发布（同步，超时时间需设长） */
export const publishSingle = (params: {
  account_id: string
  title: string
  description: string
  price: number
  original_price?: number | null
  stock?: number
  category?: string
  images: string[]        // 本地绝对路径，由 uploadProductImages 返回
  address?: string
  delivery_method?: string
  postage?: number
  brand?: string
  condition?: string
}): Promise<PublishSingleResponse> =>
  post(`${PREFIX}/publish/single`, params, { timeout: 600000 }) // 10分钟超时

/** 批量发布（异步，立即返回 batch_id） */
export const publishBatch = (params: {
  account_ids: string[]
  material_ids: number[]
}): Promise<PublishBatchResponse> => post(`${PREFIX}/publish/batch`, params)

/** 查询批量发布任务状态 */
export const getBatchStatus = (batchId: string): Promise<BatchStatusResponse> =>
  get(`${PREFIX}/publish/batch/${batchId}/status`)

// ==================== 图片上传 ====================

/** 上传商品图片（返回本地路径供 Playwright 使用 + URL 供预览）
 *  注意：不要手动设置 Content-Type，axios 会自动添加正确的 multipart boundary
 */
export const uploadProductImages = async (files: File[]): Promise<{
  success: boolean
  message: string
  data?: { paths: string[]; urls: string[] }
}> => {
  const formData = new FormData()
  files.forEach(f => formData.append('files', f))
  return post(`${PREFIX}/upload/images`, formData)
}

/** 分页查询发布日志 */
export const getPublishLogs = (
  page = 1,
  pageSize = 20,
  accountId?: string,
  status?: string
): Promise<PublishLogListResponse> => {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  if (accountId) params.append('account_id', accountId)
  if (status) params.append('status', status)
  return get(`${PREFIX}/logs?${params}`)
}

export const clearPublishLogs = async (): Promise<{ success: boolean; message: string }> => {
  return del<{ success: boolean; message: string }>(`${PREFIX}/logs/clear`)
}

// ==================== 批量导入 ====================

/** 目录扫描返回的素材项 */
export interface ScannedMaterial {
  code: string
  folder_name: string
  title: string
  description: string
  images: string[]          // 本地图片文件路径
  image_count: number
  category: string
  price: number
}

/** 扫描目录响应 */
export interface ScanDirectoryResponse {
  success: boolean
  message: string
  data?: {
    materials: ScannedMaterial[]
    total: number
  }
}

/** 扫描本地目录，解析素材 */
export const scanDirectory = (
  dirPath: string
): Promise<ScanDirectoryResponse> =>
  post(`${PREFIX}/materials/scan-directory`, { path: dirPath })

/** 批量导入确认参数 */
export interface BatchImportParams {
  materials: {
    code: string
    folder_name: string
    title: string
    description: string
    images: string[]
    price: number
    original_price?: number | null
    category: string
    condition: string
    brand: string
    delivery_method: 'express' | 'pickup'
    postage: number
    stock?: number
  }[]
}

/** 批量导入响应 */
export interface BatchImportResponse {
  success: boolean
  message: string
  data?: {
    imported: number
    failed: number
    failed_items: { code: string; reason: string }[]
  }
}

/** 批量导入素材（从本地目录） */
export const batchImportMaterials = (
  params: BatchImportParams
): Promise<BatchImportResponse> =>
  post(`${PREFIX}/materials/batch-import`, params)

// ==================== 定时发布 ====================

/** 定时规则 */
export interface PublishSchedule {
  id: number
  user_id: number
  name: string
  schedule_mode: 'once' | 'daily' | 'weekly'
  schedule_config: ScheduleConfig
  account_ids: string[]
  material_ids: number[]
  enabled: boolean
  last_triggered_at?: string | null
  next_trigger_at?: string | null
  account_count?: number
  material_count?: number
  created_at: string
  updated_at: string
}

/** 调度时间配置 */
export interface ScheduleConfig {
  datetime?: string           // once模式
  times?: string[]            // 时间点列表
  days?: number[]             // 星期几 1-7
  time_range?: { start: string; end: string }
  random?: boolean
}

/** 创建定时规则参数 */
export interface CreateScheduleParams {
  name: string
  schedule_mode: 'once' | 'daily' | 'weekly'
  schedule_config: ScheduleConfig
  account_ids: string[]
  material_ids: number[]
}

/** 更新定时规则参数 */
export interface UpdateScheduleParams {
  name?: string
  schedule_mode?: string
  schedule_config?: Record<string, unknown>
  account_ids?: string[]
  material_ids?: number[]
  enabled?: boolean
}

/** 执行记录 */
export interface PublishScheduleLog {
  id: number
  schedule_id: number
  batch_id?: string | null
  scheduled_at: string
  executed_at?: string | null
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  total_count: number
  success_count: number
  failed_count: number
  error_message?: string | null
  created_at: string
}

/** 规则列表响应 */
export interface ScheduleListResponse {
  success: boolean
  message: string
  data: {
    list: PublishSchedule[]
    total: number
    page: number
    page_size: number
    total_pages: number
  }
}

/** 执行日志列表响应 */
export interface ScheduleLogListResponse {
  success: boolean
  message: string
  data: {
    list: PublishScheduleLog[]
    total: number
    page: number
    page_size: number
    total_pages: number
  }
}

const SCHEDULE_PREFIX = '/api/v1/product-publish/schedules'

/** 创建定时规则 */
export const createSchedule = (params: CreateScheduleParams): Promise<ApiResponse> =>
  post(SCHEDULE_PREFIX, params)

/** 分页查询定时规则 */
export const getSchedules = (page = 1, pageSize = 20): Promise<ScheduleListResponse> => {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  return get(`${SCHEDULE_PREFIX}?${params}`)
}

/** 查询单条规则 */
export const getSchedule = (id: number): Promise<ApiResponse> =>
  get(`${SCHEDULE_PREFIX}/${id}`)

/** 更新定时规则 */
export const updateSchedule = (id: number, params: UpdateScheduleParams): Promise<ApiResponse> =>
  put(`${SCHEDULE_PREFIX}/${id}`, params)

/** 删除定时规则 */
export const deleteSchedule = (id: number): Promise<ApiResponse> =>
  del(`${SCHEDULE_PREFIX}/${id}`)

/** 切换规则启用/禁用 */
export const toggleSchedule = (id: number): Promise<ApiResponse> =>
  patch(`${SCHEDULE_PREFIX}/${id}/toggle`)

/** 手动触发一次 */
export const triggerSchedule = (id: number): Promise<ApiResponse> =>
  post(`${SCHEDULE_PREFIX}/${id}/trigger`)

/** 查询某规则的执行历史 */
export const getScheduleLogs = (scheduleId: number, page = 1, pageSize = 20): Promise<ScheduleLogListResponse> => {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  return get(`${SCHEDULE_PREFIX}/${scheduleId}/logs?${params}`)
}

/** 查询全部定时规则的执行历史（全局视图） */
export const getAllScheduleLogs = (page = 1, pageSize = 20): Promise<ScheduleLogListResponse> => {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  return get(`${SCHEDULE_PREFIX}/logs/global?${params}`)
}
