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

export interface PlatformMaterialAttribute {
  property_id?: string | null
  property_name?: string | null
  value_id?: string | null
  value_name?: string | null
  text?: string | null
  properties?: string | null
}

export interface PlatformCategoryPathItem {
  id: string
  name: string
}

export interface PlatformCategoryCandidate {
  cat_id?: string | null
  cat_name?: string | null
  channel_cat_id?: string | null
  channel_cat_name?: string | null
  leaf_id?: string | null
  tb_cat_id?: string | null
  path: PlatformCategoryPathItem[]
  score?: number | null
  is_selected?: boolean
}

export interface PlatformCategoryPropertyOption {
  property_id: string
  property_name: string
  value_id?: string | null
  value_name: string
  channel_cat_id?: string | null
  tb_cat_id?: string | null
}

export interface PlatformCategoryProperty {
  property_id: string
  property_name: string
  input_word?: string | null
  is_multiple?: boolean
  is_decisive_property?: boolean
  options: PlatformCategoryPropertyOption[]
}

export interface PlatformCategoryCardValue {
  catId?: string | null
  catName?: string | null
  channelCatId?: string | null
  channelCatName?: string | null
  tbCatId?: string | null
  isClicked?: string | null
  isUserClick?: string | null
  [key: string]: unknown
}

export interface PlatformCategoryCardData {
  propertyId?: string | null
  propertyName?: string | null
  valuesList?: PlatformCategoryCardValue[]
  [key: string]: unknown
}

export interface PlatformCategoryRecommendData {
  candidates: PlatformCategoryCandidate[]
  properties: PlatformCategoryProperty[]
  card_list?: PlatformCategoryCardData[]
  account_id?: string
}

export interface MaterialVideo {
  url: string
  path?: string | null
  name?: string | null
  size?: number | null
  file_id?: string | null
  width?: number | null
  height?: number | null
  duration_ms?: number | null
}

export interface PublishSpecificationValue {
  name: string
  image?: string | null
}

export interface PublishSpecification {
  name: string
  values: PublishSpecificationValue[]
  support_image?: boolean
}

export interface PublishSkuRow {
  specs: Record<string, string>
  price: number
  stock: number
}

export interface ProductMaterial {
  id: number
  user_id: number
  username?: string  // 管理员场景返回
  title: string
  description: string
  price: number
  original_price?: number | null
  category?: string | null
  platform_category_id?: string | null
  platform_category_name?: string | null
  platform_channel_category_id?: string | null
  platform_channel_category_name?: string | null
  platform_leaf_id?: string | null
  platform_tb_category_id?: string | null
  platform_category_path: PlatformCategoryPathItem[]
  platform_attributes: PlatformMaterialAttribute[]
  category_source: 'manual' | 'recommendation'
  category_confidence?: number | null
  images: string[]
  videos: MaterialVideo[]
  specifications: PublishSpecification[]
  sku_rows: PublishSkuRow[]
  quantity: number
  delivery_method: 'express' | 'pickup'
  shipping_method: 'free' | 'distance' | 'fixed' | 'template' | 'none'
  support_pickup: boolean
  postage: number
  address?: string | null
  address_expected_text?: string | null
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
  category?: string | null
  platform_category_id?: string | null
  platform_category_name?: string | null
  platform_channel_category_id?: string | null
  platform_channel_category_name?: string | null
  platform_leaf_id?: string | null
  platform_tb_category_id?: string | null
  platform_category_path?: PlatformCategoryPathItem[]
  platform_attributes?: PlatformMaterialAttribute[]
  category_source?: 'manual' | 'recommendation'
  category_confidence?: number | null
  images: string[]
  videos?: MaterialVideo[]
  specifications?: PublishSpecification[]
  sku_rows?: PublishSkuRow[]
  quantity?: number
  delivery_method?: 'express' | 'pickup'
  shipping_method?: 'free' | 'distance' | 'fixed' | 'template' | 'none'
  support_pickup?: boolean
  postage?: number
  address?: string | null
  address_expected_text?: string | null
  brand?: string | null
  condition?: string
  stock?: number
  remark?: string | null
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

export interface PublishCommissionConfig {
  title: string
  default_title: string
  tips: string
  percent: string
  max_commission: string
  tip_url: string
}

export interface PublishAccountCapability {
  account_id: string
  is_fish_shop: boolean
  support_sku_or_inventory: boolean
  commission_config: PublishCommissionConfig
}

export interface PublishBatchResponseData {
  batch_id: string
  total: number
}

export type PublishBatchResponse = ApiResponse<PublishBatchResponseData>

/** 根据商品标题和描述推荐闲鱼平台分类。 */
export const recommendPlatformCategory = (params: {
  title: string
  description: string
  account_id?: string
  current_card_list?: PlatformCategoryCardData[]
  selected_list?: Record<string, unknown>[]
  cat_id?: string
  cat_name?: string
  channel_cat_id?: string
}): Promise<ApiResponse<PlatformCategoryRecommendData>> =>
  post(`${PREFIX}/category/recommend`, params)

// ==================== 素材库接口 ====================

/** 创建素材 */
export const createMaterial = (params: MaterialCreateParams): Promise<ApiResponse> =>
  post(`${PREFIX}/materials`, params)

/** 分页查询素材列表 */
export const getMaterials = (
  page = 1,
  pageSize = 20,
  filters?: { title?: string; keyword?: string; category?: string; condition?: string; platform_category_id?: string }
): Promise<MaterialListResponse> => {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  if (filters?.title) params.append('title', filters.title)
  if (filters?.keyword) params.append('keyword', filters.keyword)
  if (filters?.category) params.append('category', filters.category)
  if (filters?.condition) params.append('condition', filters.condition)
  if (filters?.platform_category_id) params.append('platform_category_id', filters.platform_category_id)
  return get(`${PREFIX}/materials?${params}`)
}

/** 查询素材ID列表（无分页，供"全选所有素材"使用，筛选条件与分页列表一致） */
export const getAllMaterialIds = (
  filters?: { title?: string; keyword?: string; category?: string; condition?: string; platform_category_id?: string }
): Promise<ApiResponse<{ ids: number[] }>> => {
  const params = new URLSearchParams()
  if (filters?.title) params.append('title', filters.title)
  if (filters?.keyword) params.append('keyword', filters.keyword)
  if (filters?.category) params.append('category', filters.category)
  if (filters?.condition) params.append('condition', filters.condition)
  if (filters?.platform_category_id) params.append('platform_category_id', filters.platform_category_id)
  const qs = params.toString()
  return get(`${PREFIX}/materials/ids${qs ? `?${qs}` : ''}`)
}

/** 获取单条素材详情 */
export const getMaterial = (id: number): Promise<ApiResponse<ProductMaterial>> =>
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

/** 查询账号是否开通鱼小铺及其发布能力。 */
export const getPublishAccountCapability = (
  accountId: string,
): Promise<ApiResponse<PublishAccountCapability>> =>
  get(`${PREFIX}/accounts/${encodeURIComponent(accountId)}/capability`)

/** 单品发布（同步调用闲鱼发布接口） */
export const publishSingle = (params: {
  account_id: string
  title: string
  description: string
  price: number
  original_price?: number | null
  category?: string
  platform_category_id?: string | null
  platform_category_name?: string | null
  platform_channel_category_id?: string | null
  platform_channel_category_name?: string | null
  platform_leaf_id?: string | null
  platform_tb_category_id?: string | null
  platform_category_path?: PlatformCategoryPathItem[]
  platform_attributes?: PlatformMaterialAttribute[]
  category_source?: 'manual' | 'recommendation'
  category_confidence?: number | null
  images: string[]        // 本地绝对路径，由 uploadProductImages 返回
  videos?: MaterialVideo[]
  quantity?: number
  specifications?: PublishSpecification[]
  sku_rows?: PublishSkuRow[]
  stock?: number
  address?: string
  address_expected_text?: string
  delivery_method?: string
  shipping_method?: 'free' | 'distance' | 'fixed' | 'template' | 'none'
  support_pickup?: boolean
  postage?: number
  brand?: string
  condition?: string
}): Promise<PublishSingleResponse> =>
  post(`${PREFIX}/publish/single`, params, { timeout: 90000 })

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

/** 上传商品视频，返回本地路径和预览地址。 */
export const uploadProductVideos = async (files: File[]): Promise<{
  success: boolean
  message: string
  data?: { videos: MaterialVideo[]; paths: string[]; urls: string[] }
}> => {
  const formData = new FormData()
  files.forEach((file) => formData.append('files', file))
  return post(`${PREFIX}/upload/videos`, formData)
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

/** 清空发布日志（可指定保留最近 N 天，不传则清空全部） */
export const clearPublishLogs = async (days?: number): Promise<{ success: boolean; message: string }> => {
  const params = new URLSearchParams()
  if (days !== undefined && days !== null && days > 0) params.set('days', String(days))
  const qs = params.toString()
  return post<{ success: boolean; message: string }>(`${PREFIX}/logs/clear${qs ? `?${qs}` : ''}`)
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

/** 批量导入素材（客户端上传文件，支持远程访问） */
export const batchImportMaterialsUpload = (
  formData: FormData
): Promise<BatchImportResponse> =>
  post(`${PREFIX}/materials/batch-import-upload`, formData, { timeout: 300000 })

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
  publish_mode: 'specified' | 'random'
  random_count?: number | null
  deduplicate_enabled: boolean
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
  publish_mode?: 'specified' | 'random'
  random_count?: number | null
  deduplicate_enabled?: boolean
}

/** 更新定时规则参数 */
export interface UpdateScheduleParams {
  name?: string
  schedule_mode?: string
  schedule_config?: Record<string, unknown>
  account_ids?: string[]
  material_ids?: number[]
  publish_mode?: string
  random_count?: number | null
  deduplicate_enabled?: boolean
  enabled?: boolean
}

/** 执行记录 */
export interface PublishScheduleLog {
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
  detail_json?: ScheduleLogDetail | null
  created_at: string
}

/** 执行记录明细（detail_json） */
export interface ScheduleLogDetail {
  publish_mode?: string
  random_count?: number | null
  deduplicate?: boolean
  target_ok?: number
  detail_truncated?: boolean
  filtered_count?: number
  rounds?: Array<{
    round: number
    materials: Array<{
      material_id?: number
      title?: string
      item_no?: string | null
      result: 'success' | 'failed' | 'account_error'
      accounts?: Array<{ account_id: string; status: string; error?: string }>
      /** 明细过大压缩后：仅保留各账号状态计数（accounts 数组被丢弃） */
      account_counts?: { success: number; failed: number; account_error: number }
    }>
  }>
  filtered?: Array<{ material_id?: number; item_no?: string; round?: number }>
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

/** 清空定时发布执行日志（days>0 只清 N 天前的，不传或 0 清空全部） */
export const clearScheduleLogs = async (days?: number): Promise<{ success: boolean; message: string }> => {
  const qs = days && days > 0 ? `?days=${days}` : ''
  return post<{ success: boolean; message: string }>(`${SCHEDULE_PREFIX}/logs/clear${qs}`)
}

// ==================== 定时发布实时进度 ====================

/** 活跃的定时发布任务进度 */
export interface ActiveScheduleProgress {
  schedule_log_id: number
  schedule_id: number
  schedule_name: string
  batch_id: string | null
  scheduled_at: string | null
  progress: {
    total: number
    success: number
    failed: number
    publishing: number
    pending: number
    finished: boolean
    account_statuses: BatchAccountStatus[]
  } | null
}

/** 活跃任务进度列表响应 */
export interface ActiveProgressResponse {
  success: boolean
  message: string
  data: {
    tasks: ActiveScheduleProgress[]
  }
}

/** 查询当前用户所有正在执行的定时发布任务实时进度 */
export const getActiveScheduleProgress = (): Promise<ActiveProgressResponse> =>
  get(`${SCHEDULE_PREFIX}/active-progress`)
