/**
 * 定时规则创建/编辑弹窗
 *
 * 功能：
 * 1. 设置规则名称、重复模式（单次/每天/每周）
 * 2. 配置时间：指定时间点 或 时间段内随机
 * 3. 选择账号和素材
 * 4. 创建/更新定时规则
 */
import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { Clock, X, Loader2, Save, ChevronLeft, ChevronRight, Search, Image, FileText, CalendarClock, SlidersHorizontal, Users } from 'lucide-react'
import { useUIStore } from '@/store/uiStore'
import { SegmentedControl } from '@/components/common/SegmentedControl'
import { getAccountDetails } from '@/api/accounts'
import { getMaterials, getAllMaterialIds, createSchedule, updateSchedule, getWeightAlgorithmOptions, type ProductMaterial, type PublishSchedule, type ScheduleConfig, type CreateScheduleParams, type UpdateScheduleParams, type WeightAlgorithmOption } from '@/api/productPublish'

interface Props {
  initial?: PublishSchedule | null
  /** 预填账号/素材（从批量发布页跳转） */
  prefills?: { accountIds?: string[]; materialIds?: number[] }
  onClose: () => void
  onSaved: () => void
}

type ScheduleMode = 'once' | 'daily' | 'weekly'
type TimeMode = 'fixed' | 'random'
type PublishMode = 'specified' | 'random'

const WEEKDAY_LABELS = ['一', '二', '三', '四', '五', '六', '日']

// 算法类型注册表（新增算法类型时在此登记）
const ALGORITHM_TYPES: Array<{ value: string; label: string }> = [
  { value: 'heat_weight', label: '热度加权' },
]
const DEFAULT_TIME_RANGE = { start: '09:00', end: '21:00' }

export function ScheduleFormModal({ initial, prefills, onClose, onSaved }: Props) {
  const { addToast } = useUIStore()
  const [loading, setLoading] = useState(false)
  const [accounts, setAccounts] = useState<any[]>([])
  const [materials, setMaterials] = useState<ProductMaterial[]>([])
  const [dataLoading, setDataLoading] = useState(true)

  const [name, setName] = useState(initial?.name || '')
  const [publishMode, setPublishMode] = useState<PublishMode>(initial?.publish_mode || 'specified')
  const [randomCount, setRandomCount] = useState<number>(initial?.random_count || 1)
  const [deduplicate, setDeduplicate] = useState<boolean>(initial?.deduplicate_enabled || false)
  const [weightAlgorithmId, setWeightAlgorithmId] = useState<number | null>(initial?.weight_algorithm_id ?? null)
  const [weightAlgorithmType, setWeightAlgorithmType] = useState<string>('heat_weight')
  const [weightAlgorithms, setWeightAlgorithms] = useState<WeightAlgorithmOption[]>([])
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>(initial?.schedule_mode || 'daily')
  const [timeMode, setTimeMode] = useState<TimeMode>(() => {
    if (initial?.schedule_config?.random) return 'random'
    return 'fixed'
  })
  const [times, setTimes] = useState<string[]>(initial?.schedule_config?.times || ['20:00'])
  const [timeRange, setTimeRange] = useState(initial?.schedule_config?.time_range || DEFAULT_TIME_RANGE)
  const [days, setDays] = useState<number[]>(initial?.schedule_config?.days || [1, 2, 3, 4, 5])
  const [onceDatetime, setOnceDatetime] = useState(initial?.schedule_config?.datetime?.slice(0, 16) || '')
  const [selectedAccounts, setSelectedAccounts] = useState<Set<string>>(
    new Set(initial?.account_ids || prefills?.accountIds || [])
  )
  const [selectedMaterials, setSelectedMaterials] = useState<Set<number>>(
    new Set(initial?.material_ids || prefills?.materialIds || [])
  )
  const [materialSearch, setMaterialSearch] = useState('')
  const [materialPage, setMaterialPage] = useState(1)
  const [materialPageSize, setMaterialPageSize] = useState(10)
  const [materialTotal, setMaterialTotal] = useState(0)
  const [materialTotalPages, setMaterialTotalPages] = useState(0)
  const [materialLoading, setMaterialLoading] = useState(false)

  /** 加载素材（分页） */
  const loadMaterials = async (p = materialPage, size = materialPageSize) => {
    setMaterialLoading(true)
    try {
      const filters: { title?: string } = {}
      if (materialSearch.trim()) filters.title = materialSearch.trim()
      const res = await getMaterials(p, size, Object.keys(filters).length > 0 ? filters : undefined)
      if (res.success) {
        setMaterials(res.data.list)
        setMaterialTotal(res.data.total)
        setMaterialTotalPages(res.data.total_pages)
      }
    } catch { /* ignore */ }
    finally { setMaterialLoading(false) }
  }

  useEffect(() => {
    Promise.all([
      getAccountDetails(),
      loadMaterials(1, materialPageSize),
    ]).then(([accList]) => {
      setAccounts(accList)
    }).finally(() => setDataLoading(false))
    // 加载权重算法选项（随机模式下拉）；按"算法类型 → 算法"两级选择
    const applyOptions = (rawList: WeightAlgorithmOption[]) => {
      let list = rawList
      // 规则引用的算法已停用/被删（选项接口只返回启用中的）：补一个占位选项，
      // 否则受控 select 的 value 匹配不到任何 option 会渲染成空白
      const referenced = initial?.weight_algorithm_id
      if (referenced != null && !list.some(a => a.id === referenced)) {
        list = [...list, {
          id: referenced,
          name: `算法#${referenced}（不可用）`,
          algorithm_type: 'heat_weight',
          is_builtin: false,
        }]
      }
      setWeightAlgorithms(list)
      const chosen = referenced != null
        ? list.find(a => a.id === referenced)
        : list.find(a => a.is_builtin)
      if (chosen) {
        setWeightAlgorithmType(chosen.algorithm_type || 'heat_weight')
        setWeightAlgorithmId(prev => prev ?? chosen.id)
      } else if (list[0]) {
        setWeightAlgorithmType(list[0].algorithm_type || 'heat_weight')
        setWeightAlgorithmId(prev => prev ?? list[0].id)
      }
    }
    getWeightAlgorithmOptions()
      .then(res => {
        if (res.success) {
          applyOptions(res.data?.list ?? [])
        } else {
          applyOptions([])
          addToast({ type: 'warning', message: `加载权重算法失败：${res.message || '未知错误'}` })
        }
      })
      .catch(() => {
        applyOptions([])
        addToast({ type: 'error', message: '加载权重算法失败，请检查后端服务是否已重启' })
      })
  }, [])

  useEffect(() => {
    if (!dataLoading) loadMaterials(materialPage, materialPageSize)
  }, [materialPage, materialPageSize])

  const buildConfig = (): ScheduleConfig => {
    const config: ScheduleConfig = {}
    if (scheduleMode === 'once') {
      if (onceDatetime) config.datetime = onceDatetime
      return config
    }
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
    if (selectedMaterials.size === 0) { addToast({ type: 'warning', message: '请至少选择一条素材' }); return }

    if (scheduleMode === 'once' && !onceDatetime) {
      addToast({ type: 'warning', message: '请设置执行时间' }); return
    }
    if (scheduleMode !== 'once' && timeMode === 'fixed' && times.filter(t => t.trim()).length === 0) {
      addToast({ type: 'warning', message: '请至少设置一个时间点' }); return
    }

    if (publishMode === 'random') {
      if (!randomCount || randomCount < 1) {
        addToast({ type: 'warning', message: '随机发布数量至少为 1' }); return
      }
      if (randomCount > selectedMaterials.size) {
        addToast({ type: 'warning', message: `随机发布数量不能超过所选素材数（${selectedMaterials.size}）` }); return
      }
    }

    setLoading(true)
    try {
      const params: CreateScheduleParams = {
        name: name.trim(),
        schedule_mode: scheduleMode,
        schedule_config: buildConfig(),
        account_ids: Array.from(selectedAccounts),
        material_ids: Array.from(selectedMaterials),
        publish_mode: publishMode,
        random_count: publishMode === 'random' ? randomCount : null,
        deduplicate_enabled: publishMode === 'random' ? deduplicate : false,
        weight_algorithm_id: publishMode === 'random' ? weightAlgorithmId : null,
      }

      if (initial) {
        const res = await updateSchedule(initial.id, params as UpdateScheduleParams)
        if (res.success) {
          addToast({ type: 'success', message: '规则已更新' })
          onSaved()
        } else {
          addToast({ type: 'error', message: res.message || '更新失败' })
        }
      } else {
        const res = await createSchedule(params)
        if (res.success) {
          addToast({ type: 'success', message: '定时规则创建成功' })
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
  const toggleMaterial = (id: number) => {
    setSelectedMaterials(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  }
  const toggleAllAccounts = () => {
    if (selectedAccounts.size === accounts.length) setSelectedAccounts(new Set())
    else setSelectedAccounts(new Set(accounts.map((a: any) => a.id)))
  }
  const [selectAllLoading, setSelectAllLoading] = useState(false)

  /** 全选/取消全选所有素材（跨分页，按当前搜索条件） */
  const toggleAllMaterials = async () => {
    if (selectAllLoading) return
    setSelectAllLoading(true)
    try {
      const filters: { title?: string } = {}
      if (materialSearch.trim()) filters.title = materialSearch.trim()
      const res = await getAllMaterialIds(Object.keys(filters).length > 0 ? filters : undefined)
      const allIds: number[] = res.success ? (res.data?.ids || []) : []
      if (allIds.length === 0) {
        addToast({ type: 'warning', message: '当前筛选条件下没有素材' })
        return
      }
      const allSelected = allIds.every(id => selectedMaterials.has(id))
      setSelectedMaterials(prev => {
        const n = new Set(prev)
        allIds.forEach(id => allSelected ? n.delete(id) : n.add(id))
        return n
      })
      addToast(allSelected
        ? { type: 'success', message: `已取消全选 ${allIds.length} 条素材` }
        : { type: 'success', message: `已全选 ${allIds.length} 条素材` })
    } catch {
      addToast({ type: 'error', message: '获取素材列表失败，请重试' })
    } finally {
      setSelectAllLoading(false)
    }
  }

  const handleMaterialSearch = () => {
    setMaterialPage(1)
    loadMaterials(1, materialPageSize)
  }

  const handleMaterialPageSizeChange = (size: number) => {
    setMaterialPageSize(size)
    setMaterialPage(1)
  }

  const allCurrentMaterialsSelected = materials.length > 0 && materials.every(m => selectedMaterials.has(m.id))

  const totalPublishes = selectedAccounts.size * (
    publishMode === 'random' ? Math.min(randomCount || 0, selectedMaterials.size) : selectedMaterials.size
  )

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
            <Clock className="w-5 h-5" />
            {initial ? '编辑定时规则' : '新建定时规则'}
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
                <input className="input-ios" placeholder="如：晚间随机发布" value={name}
                  onChange={e => setName(e.target.value)} maxLength={100} />
              </div>
              <div className="input-group">
                <label className="input-label">重复模式</label>
                <SegmentedControl<ScheduleMode>
                  options={[
                    { value: 'once', label: '仅一次' },
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

              {scheduleMode === 'once' ? (
                <div className="input-group">
                  <label className="input-label">执行时间 <span className="text-red-500">*</span></label>
                  <input type="datetime-local" className="input-ios"
                    value={onceDatetime} onChange={e => setOnceDatetime(e.target.value)} />
                </div>
              ) : (
                <>
                  {/* 时间模式 */}
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
                </>
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

          {/* 发布策略 */}
          <div className="vben-card">
            <div className="vben-card-header">
              <h3 className="vben-card-title text-sm"><SlidersHorizontal className="w-4 h-4" />发布策略</h3>
            </div>
            <div className="vben-card-body space-y-3">
              <SegmentedControl<PublishMode>
                options={[
                  { value: 'specified', label: '指定发布', desc: '发布全部所选素材' },
                  { value: 'random', label: '随机发布', desc: '每次触发随机选 N 条' },
                ]}
                value={publishMode}
                onChange={setPublishMode}
              />

              {publishMode === 'random' && (
                <>
                  <div className="flex items-center gap-2">
                    <label className="input-label text-sm">每次随机发布</label>
                    <input type="number" min={1} max={selectedMaterials.size || 1} className="input-ios w-24"
                      value={randomCount} onChange={e => setRandomCount(Math.max(1, parseInt(e.target.value) || 1))} />
                    <span className="text-sm text-slate-500">条（发布不足自动补发）</span>
                  </div>
                  <label className="flex items-start gap-2 cursor-pointer">
                    <input type="checkbox" className="w-4 h-4 mt-0.5 text-blue-600 rounded"
                      checked={deduplicate} onChange={e => setDeduplicate(e.target.checked)} />
                    <span className="text-sm text-slate-600 dark:text-slate-300">
                      启用去重：发布前刷新账号在售商品，素材标题前缀编号（A+数字）已在售的不再发布
                    </span>
                  </label>
                  <div className="flex items-center gap-2">
                    <label className="input-label text-sm whitespace-nowrap">算法类型</label>
                    <select className="input-ios w-32" value={weightAlgorithmType}
                      onChange={e => {
                        const t = e.target.value
                        setWeightAlgorithmType(t)
                        const firstOfType = weightAlgorithms.find(a => (a.algorithm_type || 'heat_weight') === t && a.is_builtin)
                          ?? weightAlgorithms.find(a => (a.algorithm_type || 'heat_weight') === t)
                        setWeightAlgorithmId(firstOfType?.id ?? null)
                      }}>
                      {/* 注册表类型 + 数据中出现的类型（防未来新增类型时下拉空白） */}
                      {Array.from(new Set([
                        ...ALGORITHM_TYPES.map(t => t.value),
                        ...weightAlgorithms.map(a => a.algorithm_type || 'heat_weight'),
                      ])).map(v => (
                        <option key={v} value={v}>
                          {ALGORITHM_TYPES.find(t => t.value === v)?.label ?? v}
                        </option>
                      ))}
                    </select>
                    <select className="input-ios flex-1" value={weightAlgorithmId ?? ''}
                      onChange={e => setWeightAlgorithmId(e.target.value ? Number(e.target.value) : null)}>
                      {(() => {
                        const typedList = weightAlgorithms.filter(a => (a.algorithm_type || 'heat_weight') === weightAlgorithmType)
                        // 兜底：按类型过滤为空时显示全部，避免下拉空白
                        const displayList = typedList.length > 0 ? typedList : weightAlgorithms
                        if (displayList.length === 0) {
                          return <option value="">暂无算法（请先在「优化算法」中新建）</option>
                        }
                        return displayList.map(a => (
                          <option key={a.id} value={a.id} title={a.description || undefined}>
                            {a.name}{a.is_builtin ? '（内置）' : ''}
                          </option>
                        ))
                      })()}
                    </select>
                  </div>
                </>
              )}
            </div>
          </div>

          {/* 选择账号 */}
          <div className="vben-card">
            <div className="vben-card-header">
              <h3 className="vben-card-title text-sm"><Users className="w-4 h-4" />选择账号</h3>
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

          {/* 选择素材 */}
          <div className="vben-card flex flex-col">
            <div className="vben-card-header">
              <h3 className="vben-card-title text-sm"><Image className="w-4 h-4" />选择素材</h3>
              <div className="flex items-center gap-2">
                <button className="text-sm text-blue-500 hover:underline flex items-center gap-1" onClick={toggleAllMaterials} disabled={selectAllLoading}>
                  {selectAllLoading && <Loader2 className="w-3 h-3 animate-spin" />}
                  {allCurrentMaterialsSelected && materials.length > 0 ? '取消全选' : '全选所有'}
                </button>
                <span className="badge-primary text-xs">已选 {selectedMaterials.size} 条</span>
              </div>
            </div>
            <div className="vben-card-body pt-0">
              {/* 搜索栏 */}
              <div className="flex items-center gap-2 mb-2">
                <input className="input-ios flex-1" placeholder="搜索素材标题..."
                  value={materialSearch} onChange={e => setMaterialSearch(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleMaterialSearch()} />
                <button className="btn-ios-primary btn-sm" onClick={handleMaterialSearch}>
                  <Search className="w-3.5 h-3.5" />搜索
                </button>
                {materialSearch && (
                  <button className="btn-ios-secondary btn-sm" onClick={() => { setMaterialSearch(''); setMaterialPage(1); loadMaterials(1, materialPageSize); }}>
                    <X className="w-3.5 h-3.5" />重置
                  </button>
                )}
              </div>

              {/* 素材表格 */}
              <div className="max-h-64 overflow-y-auto border border-slate-200 dark:border-slate-700 rounded-lg">
                <table className="table-ios">
                  <thead className="sticky top-0 bg-white dark:bg-slate-800 z-10">
                    <tr>
                      <th className="w-10">
                        <input type="checkbox" checked={allCurrentMaterialsSelected && materials.length > 0}
                          onChange={toggleAllMaterials}
                          className="w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" />
                      </th>
                      <th>标题</th>
                      <th>价格</th>
                      <th>分类</th>
                      <th>成色</th>
                      <th>图片</th>
                    </tr>
                  </thead>
                  <tbody>
                    {materialLoading ? (
                      <tr><td colSpan={6} className="text-center py-8">
                        <Loader2 className="w-5 h-5 animate-spin text-blue-500 mx-auto" />
                      </td></tr>
                    ) : materials.length === 0 ? (
                      <tr><td colSpan={6} className="text-center py-8 text-slate-400">
                        <div className="flex flex-col items-center gap-1">
                          <Image className="w-8 h-8 text-slate-300" />
                          <p className="text-sm">没有匹配的素材</p>
                        </div>
                      </td></tr>
                    ) : materials.map(m => (
                      <tr key={m.id} className={selectedMaterials.has(m.id) ? 'bg-blue-50 dark:bg-blue-900/10' : ''}>
                        <td>
                          <input type="checkbox" checked={selectedMaterials.has(m.id)}
                            onChange={() => toggleMaterial(m.id)}
                            className="w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" />
                        </td>
                        <td className="max-w-[160px]">
                          <span className="truncate block font-medium text-slate-800 dark:text-slate-100 text-sm" title={m.title}>{m.title}</span>
                        </td>
                        <td>
                          <span className="text-amber-600 font-medium text-sm">¥{m.price}</span>
                          {m.original_price && (
                            <span className="text-xs text-slate-400 line-through ml-1">¥{m.original_price}</span>
                          )}
                        </td>
                        <td className="text-sm text-slate-500">{m.category || '-'}</td>
                        <td><span className="badge-gray text-xs">{m.condition}</span></td>
                        <td><span className="badge-info text-xs">{(m.images || []).length} 张</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* 分页 */}
              {materialTotal > 0 && (
                <div className="flex items-center justify-between pt-2 text-sm text-slate-500">
                  <div className="flex items-center gap-2">
                    <span>每页</span>
                    <select value={materialPageSize} onChange={e => handleMaterialPageSizeChange(Number(e.target.value))}
                      className="px-2 py-0.5 border border-slate-300 dark:border-slate-600 rounded bg-white dark:bg-slate-800 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                      <option value={10}>10 条</option>
                      <option value={20}>20 条</option>
                      <option value={50}>50 条</option>
                      <option value={100}>100 条</option>
                    </select>
                    <span>共 {materialTotal} 条</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <span>第 {materialPage} / {materialTotalPages} 页</span>
                    <button onClick={() => setMaterialPage(p => Math.max(1, p - 1))} disabled={materialPage <= 1 || materialLoading}
                      className="p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed">
                      <ChevronLeft className="w-4 h-4" />
                    </button>
                    <button onClick={() => setMaterialPage(p => Math.min(materialTotalPages, p + 1))} disabled={materialPage >= materialTotalPages || materialLoading}
                      className="p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed">
                      <ChevronRight className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* 预览 */}
          <div className="flex items-center justify-between p-3 bg-slate-50 dark:bg-slate-800 rounded-lg">
            <span className="text-sm text-slate-500">
              {selectedAccounts.size} 账号 × {publishMode === 'random' ? randomCount : selectedMaterials.size} 素材
            </span>
            <span className="text-lg font-semibold text-blue-600 dark:text-blue-400">
              = {totalPublishes} 次发布
            </span>
          </div>
        </div>

        <div className="modal-footer flex-shrink-0">
          <button className="btn-ios-secondary" onClick={onClose}>取消</button>
          <button className="btn-ios-primary" disabled={loading || totalPublishes === 0} onClick={handleSave}>
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            {initial ? '保存修改' : '创建规则'}
          </button>
        </div>
      </motion.div>
    </div>
  )
}
