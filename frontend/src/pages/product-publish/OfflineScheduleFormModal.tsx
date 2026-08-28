/**
 * 下架规则创建/编辑弹窗
 *
 * 功能：
 * 1. 规则名称、重复模式（每天/每周）
 * 2. 时间配置：指定时间点 或 时间段随机（复用定时发布组件逻辑）
 * 3. 筛选参数：每账号下架上限 Z + 权重算法（按"算法类型 → 算法"两级选择）
 * 4. 选择账号（仅下架这些账号的商品）
 */
import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { X, Loader2, Save, PackageX, FileText, CalendarClock, SlidersHorizontal, Users, Info } from 'lucide-react'
import { useUIStore } from '@/store/uiStore'
import { SegmentedControl } from '@/components/common/SegmentedControl'
import { getAccountDetails } from '@/api/accounts'
import { ALGORITHM_TYPES, getWeightAlgorithmOptions, type WeightAlgorithmOption } from '@/api/weightAlgorithms'
import {
  createOfflineSchedule, updateOfflineSchedule,
  type OfflineSchedule, type CreateOfflineScheduleParams, type UpdateOfflineScheduleParams,
} from '@/api/offlineSchedules'
import type { ScheduleConfig } from '@/api/productPublish'

interface Props {
  initial?: OfflineSchedule | null
  onClose: () => void
  onSaved: () => void
}

type ScheduleMode = 'daily' | 'weekly'
type TimeMode = 'fixed' | 'random'

const WEEKDAY_LABELS = ['一', '二', '三', '四', '五', '六', '日']
const DEFAULT_TIME_RANGE = { start: '09:00', end: '21:00' }

// 本表单开放的算法类型（算法类型注册表统一维护在 @/api/weightAlgorithms；
// 未来新增算法类别时，在此追加即可出现在类型下拉中）
const ALLOWED_ALGORITHM_TYPES = ['delist_weight']

export function OfflineScheduleFormModal({ initial, onClose, onSaved }: Props) {
  const { addToast } = useUIStore()
  const [loading, setLoading] = useState(false)
  const [accounts, setAccounts] = useState<any[]>([])
  const [algorithms, setAlgorithms] = useState<WeightAlgorithmOption[]>([])
  const [dataLoading, setDataLoading] = useState(true)

  const [name, setName] = useState(initial?.name || '')
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>(initial?.schedule_mode || 'daily')
  const [timeMode, setTimeMode] = useState<TimeMode>(() => {
    if (initial?.schedule_config?.random) return 'random'
    return 'fixed'
  })
  const [times, setTimes] = useState<string[]>(initial?.schedule_config?.times || ['20:00'])
  const [timeRange, setTimeRange] = useState(initial?.schedule_config?.time_range || DEFAULT_TIME_RANGE)
  const [days, setDays] = useState<number[]>(initial?.schedule_config?.days || [1, 2, 3, 4, 5])
  const [selectedAccounts, setSelectedAccounts] = useState<Set<string>>(
    new Set(initial?.account_ids || [])
  )
  const [maxCount, setMaxCount] = useState<number>(initial?.max_count || 1)
  // 算法两级选择：先选算法类型，再选该类型下的具体算法（与定时发布一致，便于扩展新类别）
  const [weightAlgorithmType, setWeightAlgorithmType] = useState<string>('delist_weight')
  const [delistAlgorithmId, setDelistAlgorithmId] = useState<number | ''>(
    initial?.delist_algorithm_id ?? ''
  )

  useEffect(() => {
    // 账号 + 启用中的全部权重算法（按"算法类型 → 算法"两级选择）
    Promise.all([
      getAccountDetails(),
      getWeightAlgorithmOptions(),
    ])
      .then(([accList, algoRes]) => {
        setAccounts(accList)
        if (algoRes.success) {
          applyAlgorithmOptions(algoRes.data?.list ?? [])
        }
      })
      .catch(() => {})
      .finally(() => setDataLoading(false))
  }, [])

  /** 应用算法选项：补占位（规则引用的算法已停用/被删时），并回填当前类型 */
  const applyAlgorithmOptions = (rawList: WeightAlgorithmOption[]) => {
    let list = rawList
    const referenced = initial?.delist_algorithm_id
    if (referenced != null && !list.some(a => a.id === referenced)) {
      list = [...list, {
        id: referenced,
        name: `算法#${referenced}（不可用）`,
        algorithm_type: 'delist_weight',
        description: null,
        params: {},
        is_builtin: false,
      }]
    }
    setAlgorithms(list)
    const chosen = referenced != null
      ? list.find(a => a.id === referenced)
      : list.find(a => a.algorithm_type === 'delist_weight' && a.is_builtin)
    if (chosen) {
      setWeightAlgorithmType(chosen.algorithm_type || 'delist_weight')
      setDelistAlgorithmId(prev => (prev === '' ? chosen.id : prev))
    } else if (list[0]) {
      setWeightAlgorithmType(list[0].algorithm_type || 'delist_weight')
      setDelistAlgorithmId(prev => (prev === '' ? list[0].id : prev))
    }
  }

  const buildConfig = (): ScheduleConfig => {
    const config: ScheduleConfig = {}
    if (timeMode === 'random') {
      config.random = true
      config.time_range = timeRange
    } else {
      config.times = times.filter(t => t.trim())
    }
    if (scheduleMode === 'weekly') {
      config.days = days.length > 0 ? days : [1, 2, 3, 4, 5]
    }
    return config
  }

  const handleSave = async () => {
    if (!name.trim()) { addToast({ type: 'warning', message: '请输入规则名称' }); return }
    if (selectedAccounts.size === 0) { addToast({ type: 'warning', message: '请至少选择一个账号' }); return }
    if (maxCount < 1) { addToast({ type: 'warning', message: '下架数量上限至少为 1' }); return }
    if (timeMode === 'fixed' && times.filter(t => t.trim()).length === 0) {
      addToast({ type: 'warning', message: '请至少设置一个时间点' }); return
    }

    setLoading(true)
    try {
      const params: CreateOfflineScheduleParams = {
        name: name.trim(),
        schedule_mode: scheduleMode,
        schedule_config: buildConfig(),
        account_ids: Array.from(selectedAccounts),
        max_count: maxCount,
        delist_algorithm_id: delistAlgorithmId === '' ? null : delistAlgorithmId,
      }

      if (initial) {
        const res = await updateOfflineSchedule(initial.id, params as UpdateOfflineScheduleParams)
        if (res.success) {
          addToast({ type: 'success', message: '下架规则已更新' })
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
        className="modal-content max-w-2xl max-h-[90vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 className="modal-title flex items-center gap-2">
            <PackageX className="w-5 h-5" />
            {initial ? '编辑下架规则' : '新建下架规则'}
          </h2>
          <button className="modal-close" onClick={onClose}><X className="w-5 h-5" /></button>
        </div>
        <div className="modal-body overflow-y-auto space-y-4">

          {/* 基本信息 */}
          <div className="vben-card">
            <div className="vben-card-header">
              <h3 className="vben-card-title text-sm"><FileText className="w-4 h-4" />基本信息</h3>
            </div>
            <div className="vben-card-body space-y-3">
              <div className="input-group">
                <label className="input-label">规则名称 <span className="text-red-500">*</span></label>
                <input className="input-ios" placeholder="如：清理长期无单商品" value={name}
                  onChange={e => setName(e.target.value)} maxLength={100} />
              </div>
              <div className="input-group">
                <label className="input-label">重复模式</label>
                <SegmentedControl<ScheduleMode>
                  options={[
                    { value: 'daily', label: '每天' },
                    { value: 'weekly', label: '每周' },
                  ]}
                  value={scheduleMode}
                  onChange={setScheduleMode}
                />
              </div>
            </div>
          </div>

          {/* 执行时间 */}
          <div className="vben-card">
            <div className="vben-card-header">
              <h3 className="vben-card-title text-sm"><CalendarClock className="w-4 h-4" />执行时间</h3>
            </div>
            <div className="vben-card-body space-y-3">
              <SegmentedControl<TimeMode>
                options={[
                  { value: 'fixed', label: '指定时间点' },
                  { value: 'random', label: '时间段随机' },
                ]}
                value={timeMode}
                onChange={setTimeMode}
              />

              {timeMode === 'fixed' ? (
                <div>
                  <label className="input-label text-xs mb-1">时间点列表</label>
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
                  <button onClick={addTime} className="text-sm text-blue-500 hover:underline mt-1">
                    + 添加时间点
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-3">
                  <span className="text-sm text-slate-500">时间段:</span>
                  <input type="time" className="input-ios w-32" value={timeRange.start}
                    onChange={e => setTimeRange(prev => ({ ...prev, start: e.target.value }))} />
                  <span className="text-slate-400">~</span>
                  <input type="time" className="input-ios w-32" value={timeRange.end}
                    onChange={e => setTimeRange(prev => ({ ...prev, end: e.target.value }))} />
                  <span className="text-xs text-slate-400">（每次随机选取）</span>
                </div>
              )}

              {/* 每周选择 */}
              {scheduleMode === 'weekly' && (
                <div>
                  <label className="input-label text-xs mb-1">选择星期</label>
                  <div className="flex gap-1.5">
                    {WEEKDAY_LABELS.map((label, i) => {
                      const d = i + 1
                      const active = days.includes(d)
                      return (
                        <button key={d} type="button" onClick={() => toggleDay(d)}
                          className={`w-9 h-9 rounded-lg text-sm font-medium transition-colors ${
                            active
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

          {/* 筛选参数 */}
          <div className="vben-card">
            <div className="vben-card-header">
              <h3 className="vben-card-title text-sm"><SlidersHorizontal className="w-4 h-4" />筛选参数</h3>
            </div>
            <div className="vben-card-body space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="input-group">
                  <label className="input-label">每账号下架上限 <span className="text-red-500">*</span></label>
                  <div className="flex items-center gap-1">
                    <input type="number" min={1} className="input-ios" value={maxCount}
                      onChange={e => setMaxCount(Math.max(1, parseInt(e.target.value) || 1))} />
                    <span className="text-sm text-slate-500">个</span>
                  </div>
                </div>
                <div className="input-group">
                  <label className="input-label">权重算法</label>
                  <div className="flex items-center gap-2">
                    <select className="input-ios w-32" value={weightAlgorithmType}
                      onChange={e => {
                        const t = e.target.value
                        setWeightAlgorithmType(t)
                        const firstOfType = algorithms.find(a => (a.algorithm_type || 'delist_weight') === t && a.is_builtin)
                          ?? algorithms.find(a => (a.algorithm_type || 'delist_weight') === t)
                        setDelistAlgorithmId(firstOfType?.id ?? '')
                      }}>
                      {/* 本表单开放的类型 + 当前已选类型（编辑时引用的算法可能属于历史类型，防下拉空白） */}
                      {Array.from(new Set([...ALLOWED_ALGORITHM_TYPES, weightAlgorithmType])).map(v => (
                        <option key={v} value={v}>
                          {ALGORITHM_TYPES.find(t => t.value === v)?.label ?? v}
                        </option>
                      ))}
                    </select>
                    <select className="input-ios flex-1" value={delistAlgorithmId}
                      onChange={e => setDelistAlgorithmId(e.target.value === '' ? '' : Number(e.target.value))}>
                      {(() => {
                        const typedList = algorithms.filter(a => (a.algorithm_type || 'delist_weight') === weightAlgorithmType)
                        // 兜底：按类型过滤为空时显示全部，避免下拉空白
                        const displayList = typedList.length > 0 ? typedList : algorithms
                        if (displayList.length === 0) {
                          return <option value="">暂无算法（请先在「优化算法」中新建）</option>
                        }
                        return (
                          <>
                            <option value="">系统默认参数（不选算法）</option>
                            {displayList.map(a => (
                              <option key={a.id} value={a.id} title={a.description || undefined}>
                                {a.name}{a.is_builtin ? '（内置）' : ''}
                              </option>
                            ))}
                          </>
                        )
                      })()}
                    </select>
                  </div>
                </div>
              </div>
              <p className="flex items-start gap-1.5 text-xs bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-lg px-3 py-2">
                <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                <span>对账号内在售商品按闲鱼官方数据（真实上架天数、近7天曝光/浏览/咨询/成交/转化率、累计想要）归一化打分，权重最高的优先下架，每个账号最多下架 {maxCount} 个</span>
              </p>
            </div>
          </div>

          {/* 选择账号 */}
          <div className="vben-card">
            <div className="vben-card-header">
              <h3 className="vben-card-title text-sm"><Users className="w-4 h-4" />选择账号（仅下架这些账号的商品）</h3>
              <div className="flex items-center gap-2">
                <span className="badge-primary text-xs">已选 {selectedAccounts.size} / {accounts.length}</span>
                <button className="text-sm text-blue-500 hover:underline" onClick={toggleAllAccounts}>
                  {selectedAccounts.size === accounts.length && accounts.length > 0 ? '取消全选' : '全选'}
                </button>
              </div>
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

export default OfflineScheduleFormModal
