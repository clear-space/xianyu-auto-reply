/**
 * 系统信息 API
 *
 * 对应后端 /api/v1/admin/system-info 系列接口（管理员专用）。
 * 提供系统运行状态快照、趋势数据、存储分布、数据库详情、服务详情与告警。
 */
import { get, post } from '@/utils/request'

const PREFIX = '/api/v1/admin/system-info'

// ==================== 类型定义 ====================

export interface DiskInfo {
  mountpoint: string
  device: string
  fstype: string
  total: number
  used: number
  free: number
  percent: number
}

export interface NetInfo {
  sent_rate: number
  recv_rate: number
  sent_total: number
  recv_total: number
}

export interface HostMetrics {
  hostname: string
  boot_time: number | null
  uptime_seconds: number | null
  cpu_percent: number | null
  cpu_per_core: number[] | null
  cpu_count: number
  mem_total: number | null
  mem_used: number | null
  mem_available: number | null
  mem_percent: number | null
  process_rss: number | null
  load_avg: number[] | null
  process_count: number | null
  disk: DiskInfo[]
  net: NetInfo | null
  platform: string
}

export interface DirSizeInfo {
  path: string
  size_bytes: number
  file_count: number
}

export interface ServiceProbe {
  available: boolean
  status_code?: number | null
  latency_ms?: number
  error?: string
}

export interface SystemAlertItem {
  id: number
  alert_type: string
  level: string
  title: string
  detail: Record<string, unknown> | null
  created_at: string | null
}

export interface SystemSummary {
  host: HostMetrics
  dirs: Record<string, DirSizeInfo>
  mysql: Record<string, unknown> | null
  redis: Record<string, unknown> | null
  services: Record<string, ServiceProbe>
  active_alerts: SystemAlertItem[]
  active_alert_count: number
}

export interface MetricPoint {
  ts: string
  cpu: number | null
  cpu_max?: number | null
  mem: number | null
  mem_max?: number | null
  disk_max_percent: number | null
  net?: NetInfo | null
  sample_count?: number
}

export interface TableRankItem {
  table_name: string
  rows: number
  data_length: number
  index_length: number
}

export interface RetentionPolicyItem {
  table_name: string
  config_key: string
  retention_days: number
  rows: number | null
  oldest: string | null
  newest: string | null
}

export interface BackupFileInfo {
  name: string
  size_bytes: number
  mtime: number
}

// ==================== API 函数 ====================

/** 系统运行状态实时快照 */
export const getSystemSummary = async (): Promise<SystemSummary> => {
  const res = await get<{ success: boolean; data: SystemSummary }>(`${PREFIX}/summary`)
  if (!res.success) throw new Error('获取系统状态失败')
  return res.data
}

/** 系统指标趋势数据（hours: 1~720） */
export const getSystemMetrics = async (hours: number): Promise<MetricPoint[]> => {
  const res = await get<{ success: boolean; data: { points: MetricPoint[] } }>(
    `${PREFIX}/metrics?hours=${hours}`,
  )
  if (!res.success) throw new Error('获取趋势数据失败')
  return res.data.points || []
}

/** 存储分布（目录体积 + 备份清单） */
export const getSystemStorage = async (): Promise<{
  dirs: Record<string, DirSizeInfo>
  backups: BackupFileInfo[]
}> => {
  const res = await get<{ success: boolean; data: { dirs: Record<string, DirSizeInfo>; backups: BackupFileInfo[] } }>(
    `${PREFIX}/storage`,
  )
  if (!res.success) throw new Error('获取存储分布失败')
  return res.data
}

/** 数据库详情（TOP 表 + 保留策略生效状态） */
export const getSystemTables = async (): Promise<{
  db_total_size: number
  top_tables: TableRankItem[]
  retention_policies: RetentionPolicyItem[]
}> => {
  const res = await get<{
    success: boolean
    data: { db_total_size: number; top_tables: TableRankItem[]; retention_policies: RetentionPolicyItem[] }
  }>(`${PREFIX}/tables`)
  if (!res.success) throw new Error('获取数据库详情失败')
  return res.data
}

/** 服务与进程详情 */
export const getSystemServices = async (): Promise<Record<string, unknown>> => {
  const res = await get<{ success: boolean; data: Record<string, unknown> }>(`${PREFIX}/services`)
  if (!res.success) throw new Error('获取服务详情失败')
  return res.data
}

/** 告警列表 */
export const getSystemAlerts = async (
  statusFilter: string = 'active',
): Promise<Array<SystemAlertItem & { status: string; source: string; resolved_at: string | null }>> => {
  const res = await get<{ success: boolean; data: { alerts: Array<SystemAlertItem & { status: string; source: string; resolved_at: string | null }> } }>(
    `${PREFIX}/alerts?status_filter=${statusFilter}`,
  )
  if (!res.success) throw new Error('获取告警列表失败')
  return res.data.alerts || []
}

/** 确认告警 */
export const ackSystemAlert = async (alertId: number): Promise<{ success: boolean; message: string }> => {
  return post<{ success: boolean; message: string }>(`${PREFIX}/alerts/ack`, { alert_id: alertId })
}

// ==================== 手动清理 ====================

export interface CleanupTableItem {
  table_name: string
  deleted_rows: number
  remaining_rows: number | null
  duration_ms: number | null
  status: string
}

export interface CleanupReport {
  batch_time: string | null
  total_deleted: number
  tables: CleanupTableItem[]
  last_backup: {
    status: string
    file_name: string | null
    file_size: number | null
    duration_ms: number | null
    created_at: string | null
  } | null
}

/** 最近一批数据保留清理的审计结果（含最近一次备份结果） */
export const getCleanupReport = async (): Promise<CleanupReport> => {
  const res = await get<{ success: boolean; data: CleanupReport }>(`${PREFIX}/cleanup-report`)
  if (!res.success) throw new Error('获取清理报告失败')
  return res.data
}

/** 清空目录体积缓存并立即重新统计（清理后调用，即时反映最新体积） */
export const refreshDirs = async (): Promise<Record<string, DirSizeInfo>> => {
  const res = await post<{ success: boolean; data: { dirs: Record<string, DirSizeInfo> } }>(
    `${PREFIX}/refresh-dirs`,
  )
  if (!res.success) throw new Error('刷新存储数据失败')
  return res.data.dirs
}
