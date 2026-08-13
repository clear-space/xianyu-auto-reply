/**
 * 自动下架规则创建/编辑弹窗
 *
 * 功能：
 * 1. 设置 X（已上架天数）、Y（无订单天数）、Z（下架数量）
 * 2. 选择重复模式（每天/每周）和时间
 * 3. 选择账号
 */
import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { X, Loader2, Save, Trash2 } from 'lucide-react'
import { useUIStore } from '@/store/uiStore'
import { getAccountDetails } from '@/api/accounts'
import { createOfflineSchedule, updateOfflineSchedule, type OfflineSchedule, type CreateOfflineScheduleParams, type UpdateOfflineScheduleParams, type ScheduleConfig } from '@/api/productPublish'

interface Props {
  initial?: OfflineSchedule | null
  onClose: () => void
  onSaved: () => void
}

type ScheduleMode = 'daily' | 'weekly'
type TimeMode = 'fixed' | 'random'

const WEEKDAY_LABELS = ['一', '二', '三', '四', '五', '六', '日']
const DEFAULT_TIME_RANGE = { start: '23:00', end: '23:59' }

export function OfflineScheduleFormModal({ initial, onClose, onSaved }: Props) {
  const { addToast } = useUIStore()
  const [loading, setLoading] = useState(false)
  const [accounts, setAccounts] = useState<any[]>([])
  const [dataLoading, setDataLoading] = useState(true)

  const [name, setName] = useState(initial?.name || '')
  const [ageDays, setAgeDays] = useState(initial?.age_days ?? 7)
  const [noOrderDays, setNoOrderDays] = useState(initial?.no_order_days ?? 7)
  const [offlineCount, setOfflineCount] = useState(initial?.offline_count ?? 5)
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>(initial?.schedule_mode || 'daily')
  const [timeMode, setTimeMode] = useState<TimeMode>(() => {
    if (initial?.schedule_config?.random) return 'random'
    return 'fixed'
  })
  const [times, setTimes] = useState<string[]>(initial?.schedule_config?.times || ['23:00'])
  const [timeRange, setTimeRange] = useState(initial?.schedule_config?.time_range || DEFAULT_TIME_RANGE)
  const [days, setDays] = useState<number[]>(initial?.schedule_config?.days || [1, 2, 3, 4, 5])
  const [selectedAccounts, setSelectedAccounts] = useState<Set<string>>(
    new Set(initial?.account_ids || [])
  )

  useEffect(() => {
    getAccountDetails().then(setAccounts).finally(() => setDataLoading(false))
  }, [])

  const buildConfig = (): ScheduleConfig => {
    if (timeMode === 'random') {
      return { random: true, time_range: timeRange }
    }
    const config: ScheduleConfig = { times: times.filter(t => t.trim()) }
    if (scheduleMode === 'weekly') {
      config.days = days.length > 0 ? days : [1, 2, 3, 4, 5]
    }
    return config
  }

  const handleSave = async () => {
    if (!name.trim()) { addToast({ type: 'warning', message: '请输入规则名称' }); return }
    if (selectedAccounts.size === 0) { addToast({ type: 'warning', message: '请至少选择一个账号' }); return }
    if (ageDays < 1 || noOrderDays < 1 || offlineCount < 1) {
      addToast({ type: 'warning', message: '天数阈值和下架数量必须大于 0' }); return
    }

    setLoading(true)
    try {
      const params: CreateOfflineScheduleParams = {
        name: name.trim(),
        age_days: ageDays,
        no_order_days: noOrderDays,
        offline_count: offlineCount,
        schedule_mode: scheduleMode,
        schedule_config: buildConfig(),
        account_ids: Array.from(selectedAccounts),
      }

      if (initial) {
        const res = await updateOfflineSchedule(initial.id, params as UpdateOfflineScheduleParams)
        if (res.success) {
          addToast({ type: 'success', message: '规则已更新' })
          onSaved()
        } else {
          addToast({ type: 'error', message: res.message || '更新失败' })
        }
      } else {
        const res = await createOfflineSchedule(params)
        if (res.success) {
          addToast({ type: 'success', message: '下架规则创建成功' })
          onSaved()
        } else {
          addToast({ type: 'error', message: res.message || '创建失败' })
        }
      }
    } catch {
      addToast({ type: 'error', message: '操作失败，请重试' })
    } finally {
      setLoading(false)
    }
  }

  const toggleDay = (d: number) => {
    setDays(prev => prev.includes(d) ? prev.filter(x => x !== d) : [...prev, d].sort())
  }
  const addTime = () => setTimes([...times, '12:00'])
  const removeTime = (i: number) => setTimes(times.filter((_, idx) => idx !== i))
  const updateTime = (i: number, v: string) => setTimes(times.map((t, idx) => idx === i ? v : t))

  const toggleAccount = (id: string) => {
    setSelectedAccounts(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  }
  const toggleAllAccounts = () => {
    if (selectedAccounts.size === accounts.length) setSelectedAccounts(new Set())
    else setSelectedAccounts(new Set(accounts.map((a: any) => a.id)))
  }

  if (dataLoading) {
    return (
      <div className="modal-overlay">
        <div className="modal-content max-w-lg">
          <div className="flex justify-center py-10"><Loader2 className="w-8 h-8 animate-spin text-blue-500" /></div>
        </div>
      </div>
    )
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }}
        className="modal-content max-w-xl max-h-[90vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 className="modal-title flex items-center gap-2">
            <Trash2 className="w-5 h-5" />
            {initial ? '编辑下架规则' : '新建下架规则'}
          </h2>
          <button className="modal-close" onClick={onClose}><X className="w-5 h-5" /></button>
        </div>
        <div className="modal-body overflow-y-auto space-y-4">

          {/* 规则名称 */}
          <div className="input-group">
            <label className="input-label">规则名称 <span className="text-red-500">*</span></label>
            <input className="input-ios" placeholder="如：夜间下架旧商品" value={name}
              onChange={e => setName(e.target.value)} maxLength={100} />
          </div>

          {/* 下架参数 */}
          <div className="vben-card">
            <div className="vben-card-header">
              <h3 className="vben-card-title text-sm">下架参数</h3>
            </div>
            <div className="vben-card-body space-y-3">
              <div className="grid grid-cols-3 gap-3">
                <div className="input-group">
                  <label className="input-label">已上架天数 &gt;</label>
                  <input type="number" min={1} className="input-ios" value={ageDays}
                    onChange={e => setAgeDays(Math.max(1, parseInt(e.target.value) || 1))} />
                </div>
                <div className="input-group">
                  <label className="input-label">无订单天数</label>
                  <input type="number" min={1} className="input-ios" value={noOrderDays}
                    onChange={e => setNoOrderDays(Math.max(1, parseInt(e.target.value) || 1))} />
                </div>
                <div className="input-group">
                  <label className="input-label">下架数量上限</label>
                  <input type="number" min={1} className="input-ios" value={offlineCount}
                    onChange={e => setOfflineCount(Math.max(1, parseInt(e.target.value) || 1))} />
                </div>
              </div>
              <p className="text-xs text-slate-400">
                筛选上架超过 {ageDays} 天、且最近 {noOrderDays} 天无订单的商品，按发布时间升序取前 {offlineCount} 个下架
              </p>
            </div>
          </div>

          {/* 重复模式 */}
          <div className="input-group">
            <label className="input-label">执行频率</label>
            <div className="flex gap-2 mt-1">
              {([
                { key: 'daily', label: '每天处理一次' },
                { key: 'weekly', label: '每周处理一次' },
              ] as { key: ScheduleMode; label: string }[]).map(m => (
                <button key={m.key} type="button" onClick={() => setScheduleMode(m.key)}
                  className={`px-4 py-2 rounded-lg border text-sm transition-colors ${
                    scheduleMode === m.key
                      ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-600'
                      : 'border-slate-300 dark:border-slate-600 text-slate-600 hover:border-blue-400'
                  }`}>{m.label}</button>
              ))}
            </div>
          </div>

          {/* 时间配置 */}
          <div className="vben-card">
            <div className="vben-card-body space-y-3">
              <div className="flex gap-2">
                {([
                  { key: 'fixed', label: '指定时间点' },
                  { key: 'random', label: '时间段随机' },
                ] as { key: TimeMode; label: string }[]).map(m => (
                  <button key={m.key} type="button" onClick={() => setTimeMode(m.key)}
                    className={`px-3 py-1.5 rounded-lg border text-sm transition-colors ${
                      timeMode === m.key
                        ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-600'
                        : 'border-slate-300 dark:border-slate-600 text-slate-600 hover:border-blue-400'
                    }`}>{m.label}</button>
                ))}
              </div>

              {timeMode === 'fixed' ? (
                <div>
                  <div className="space-y-1.5">
                    {times.map((t, i) => (
                      <div key={i} className="flex items-center gap-2">
                        <input type="time" className="input-ios w-32" value={t}
                          onChange={e => updateTime(i, e.target.value)} />
                        {times.length > 1 && (
                          <button onClick={() => removeTime(i)}
                            className="text-red-400 hover:text-red-600 p-1"><X className="w-4 h-4" /></button>
                        )}
                      </div>
                    ))}
                  </div>
                  <button onClick={addTime} className="text-sm text-blue-500 hover:underline mt-1">+ 添加时间点</button>
                </div>
              ) : (
                <div className="flex items-center gap-3">
                  <span className="text-sm text-slate-500">时间段:</span>
                  <input type="time" className="input-ios w-32" value={timeRange.start}
                    onChange={e => setTimeRange(prev => ({ ...prev, start: e.target.value }))} />
                  <span className="text-slate-400">~</span>
                  <input type="time" className="input-ios w-32" value={timeRange.end}
                    onChange={e => setTimeRange(prev => ({ ...prev, end: e.target.value }))} />
                </div>
              )}

              {scheduleMode === 'weekly' && (
                <div>
                  <label className="input-label text-xs mb-1">选择星期</label>
                  <div className="flex gap-1.5">
                    {WEEKDAY_LABELS.map((label, i) => {
                      const d = i + 1
                      return (
                        <button key={d} type="button" onClick={() => toggleDay(d)}
                          className={`w-9 h-9 rounded-lg text-sm font-medium transition-colors ${
                            days.includes(d)
                              ? 'bg-blue-500 text-white'
                              : 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-400 hover:bg-slate-200'
                          }`}>{label}</button>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* 选择账号 */}
          <div className="vben-card">
            <div className="vben-card-header">
              <h3 className="vben-card-title text-sm">选择账号</h3>
              <button className="text-sm text-blue-500 hover:underline" onClick={toggleAllAccounts}>
                {selectedAccounts.size === accounts.length && accounts.length > 0 ? '取消全选' : '全选'}
              </button>
            </div>
            <div className="vben-card-body">
              {accounts.length === 0 ? (
                <p className="text-center text-slate-400 py-4 text-sm">暂无账号</p>
              ) : (
                <div className="space-y-1 max-h-40 overflow-y-auto">
                  {accounts.map((a: any) => (
                    <label key={a.id} className={`flex items-center gap-2 p-2 rounded-lg cursor-pointer transition-colors ${selectedAccounts.has(a.id) ? 'bg-blue-50 dark:bg-blue-900/20' : 'hover:bg-slate-50 dark:hover:bg-slate-700'}`}>
                      <input type="checkbox" className="w-4 h-4 text-blue-600 rounded"
                        checked={selectedAccounts.has(a.id)} onChange={() => toggleAccount(a.id)} />
                      <span className="text-sm truncate text-slate-700 dark:text-slate-200">{a.note || a.id}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="modal-footer flex-shrink-0">
          <button className="btn-ios-secondary" onClick={onClose}>取消</button>
          <button className="btn-ios-primary" disabled={loading} onClick={handleSave}>
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            {initial ? '保存修改' : '创建规则'}
          </button>
        </div>
      </motion.div>
    </div>
  )
}
