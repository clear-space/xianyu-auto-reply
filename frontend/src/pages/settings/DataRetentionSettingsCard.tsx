import { useState, type Dispatch, type SetStateAction } from 'react'
import { Database, PlayCircle } from 'lucide-react'
import type { SystemSettings } from '@/types'
import { useUIStore } from '@/store/uiStore'
import { triggerScheduledTask } from '@/api/scheduledTasks'

interface DataRetentionSettingsCardProps {
  settings: SystemSettings | null
  setSettings: Dispatch<SetStateAction<SystemSettings | null>>
}

/**
 * 数据保留策略配置卡片
 *
 * 功能：
 * 1. 配置统一数据保留引擎（scheduler 的 data_retention_cleanup 任务）对各日志表的保留天数
 * 2. 配置清理执行参数（总开关、单批行数、单轮批次数上限）
 * 3. 提供「立即执行一次清理」按钮，手动触发一轮清理
 *
 * 说明：
 * - 所有值通过页面右上角「保存设置」按钮持久化（沿用系统设置通用保存流程）
 * - 保留天数修改后，下次定时执行（默认每小时）或手动触发时生效
 * - 默认保留天数均为 30 天，客户服务器空间紧张时可下调
 */

// 保留天数配置项：(键, 中文名, 说明)
const RETENTION_DAY_ITEMS: Array<{ key: keyof SystemSettings; label: string; desc: string }> = [
  { key: 'data_retention.token_renewal_log_days', label: 'Token续期日志', desc: 'xy_scheduled_token_renewal_log（每20秒一批）' },
  { key: 'data_retention.cookies_refresh_log_days', label: 'COOKIES刷新日志', desc: 'xy_scheduled_cookies_refresh_log' },
  { key: 'data_retention.auto_reply_message_log_days', label: '自动回复/发货消息日志', desc: 'xy_auto_reply_message_logs' },
  { key: 'data_retention.default_reply_record_days', label: '默认回复发送记录', desc: 'xy_default_reply_records' },
  { key: 'data_retention.account_login_log_days', label: '账号登录日志', desc: 'xy_account_login_logs' },
  { key: 'data_retention.publish_log_days', label: '发布日志', desc: 'xy_publish_logs' },
  { key: 'data_retention.risk_control_log_days', label: '风控日志', desc: 'xy_risk_control_logs' },
  { key: 'data_retention.token_cache_soft_expired_days', label: 'Token缓存软过期缓冲', desc: 'xy_token_cache 软过期后物理删除缓冲天数' },
  { key: 'data_retention.ai_chat_message_days', label: 'AI对话消息', desc: 'xy_ai_chat_messages' },
  { key: 'data_retention.goofish_crawl_item_days', label: '采集商品', desc: 'xy_goofish_crawl_items' },
  { key: 'data_retention.scheduled_task_log_days', label: '各定时任务执行日志', desc: '补发货/补评价/擦亮/登录续期/接口续期/小红花/关闭通知/上新监控' },
  { key: 'data_retention.cleanup_log_days', label: '清理审计日志', desc: 'xy_data_cleanup_log 自身保留天数' },
]

export function DataRetentionSettingsCard({ settings, setSettings }: DataRetentionSettingsCardProps) {
  const { addToast } = useUIStore()
  const [triggering, setTriggering] = useState(false)

  const setValue = (key: keyof SystemSettings, value: string) => {
    setSettings((s) => (s ? ({ ...s, [key]: value } as SystemSettings) : s))
  }

  const handleTriggerCleanup = async () => {
    setTriggering(true)
    try {
      const result = await triggerScheduledTask('data_retention_cleanup')
      if (result.success) {
        addToast({ type: 'success', message: '清理任务已触发，正在执行...（未保存的修改请先点右上角保存设置）' })
      } else {
        addToast({ type: 'error', message: result.message || '触发清理任务失败' })
      }
    } catch {
      addToast({ type: 'error', message: '触发清理任务失败，请检查调度服务是否在线' })
    } finally {
      setTriggering(false)
    }
  }

  return (
    <div className="vben-card">
      <div className="vben-card-header">
        <h2 className="vben-card-title">
          <Database className="w-4 h-4" />
          数据保留策略
        </h2>
      </div>
      <div className="vben-card-body space-y-4">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          统一数据保留引擎每小时自动清理各日志类表超过保留天数的历史数据，防止数据库无限增长（默认保留 30 天）。
          修改后点右上角「保存设置」，下次定时执行或手动触发时生效。
        </p>

        {/* 总开关 + 执行参数 */}
        <div className="flex items-center justify-between py-3 border-b border-slate-100 dark:border-slate-700">
          <div>
            <p className="font-medium text-slate-900 dark:text-slate-100">自动清理总开关</p>
            <p className="text-sm text-slate-500 dark:text-slate-400">关闭后所有日志类数据停止自动清理（应急开关，一般保持开启）</p>
          </div>
          <label className="switch-ios">
            <input
              type="checkbox"
              checked={Boolean(settings?.['data_retention.enabled'] ?? true)}
              onChange={(e) => setSettings((s) => (s ? { ...s, 'data_retention.enabled': e.target.checked } : s))}
            />
            <span className="switch-slider"></span>
          </label>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
          <div className="flex items-center justify-between py-2 border-b border-slate-100 dark:border-slate-700">
            <div>
              <p className="font-medium text-slate-900 dark:text-slate-100">单批删除行数</p>
              <p className="text-sm text-slate-500 dark:text-slate-400">分批删除避免长事务锁表（100~100000）</p>
            </div>
            <input
              type="number"
              min={100}
              max={100000}
              value={settings?.['data_retention.cleanup_batch_size'] || '1000'}
              onChange={(e) => {
                const val = e.target.value
                if (val === '' || /^\d+$/.test(val)) {
                  setValue('data_retention.cleanup_batch_size', val)
                }
              }}
              className="input-ios w-24 text-center"
            />
          </div>
          <div className="flex items-center justify-between py-2 border-b border-slate-100 dark:border-slate-700">
            <div>
              <p className="font-medium text-slate-900 dark:text-slate-100">单表单轮批次数上限</p>
              <p className="text-sm text-slate-500 dark:text-slate-400">防止首次上线历史积压过大拖慢单轮（1~10000）</p>
            </div>
            <input
              type="number"
              min={1}
              max={10000}
              value={settings?.['data_retention.max_batches_per_table'] || '100'}
              onChange={(e) => {
                const val = e.target.value
                if (val === '' || /^\d+$/.test(val)) {
                  setValue('data_retention.max_batches_per_table', val)
                }
              }}
              className="input-ios w-24 text-center"
            />
          </div>
        </div>

        {/* 各表保留天数 */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
          {RETENTION_DAY_ITEMS.map((item) => (
            <div key={item.key} className="flex items-center justify-between py-2 border-b border-slate-100 dark:border-slate-700">
              <div>
                <p className="font-medium text-slate-900 dark:text-slate-100">{item.label}</p>
                <p className="text-sm text-slate-500 dark:text-slate-400">{item.desc}</p>
              </div>
              <div className="flex items-center gap-1">
                <input
                  type="number"
                  min={1}
                  max={3650}
                  value={(settings?.[item.key] as string | undefined) || '30'}
                  onChange={(e) => {
                    const val = e.target.value
                    if (val === '' || /^\d+$/.test(val)) {
                      setValue(item.key, val)
                    }
                  }}
                  className="input-ios w-20 text-center"
                />
                <span className="text-sm text-slate-500 dark:text-slate-400">天</span>
              </div>
            </div>
          ))}
        </div>

        {/* 手动触发 */}
        <div className="flex items-center justify-between pt-2 border-t border-slate-100 dark:border-slate-700">
          <div>
            <p className="font-medium text-slate-900 dark:text-slate-100">立即执行一次清理</p>
            <p className="text-sm text-slate-500 dark:text-slate-400">按当前保存的保留策略立即清理一轮超期数据</p>
          </div>
          <button
            type="button"
            onClick={handleTriggerCleanup}
            disabled={triggering}
            className="btn-ios-primary inline-flex items-center gap-1.5"
          >
            {triggering ? (
              <span className="animate-spin inline-block w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full" />
            ) : (
              <PlayCircle className="w-4 h-4" />
            )}
            {triggering ? '触发中...' : '立即清理'}
          </button>
        </div>
      </div>
    </div>
  )
}
