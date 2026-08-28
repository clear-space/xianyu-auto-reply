/**
 * 权重算法管理页（管理员）
 *
 * 优化算法目录下的两个同级页面共用本实现：
 * - HeatWeightAlgorithms：上架权重算法（热度加权）
 * - DelistWeightAlgorithms：下架权重算法（下架加权）
 *
 * 功能：
 * 1. 算法列表（默认折叠，防止算法新建过多占用页面；参数摘要/引用次数/启停）
 * 2. 新建/编辑算法（参数表单按类型）
 * 3. 算法说明弹窗（加权机制说明）
 * 4. 效果预览（上架=素材权重；下架=在售商品权重）
 * 5. 删除保护（被定时发布/定时下架规则引用时后端拒绝）
 */
import { useState, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Plus, Pencil, Trash2, Power, PowerOff, RefreshCw, Scale, Info, X, Loader2, Save, ChevronDown, ChevronUp, BarChart3, PackageX } from 'lucide-react'
import { useUIStore } from '@/store/uiStore'
import {
  getWeightAlgorithms, createWeightAlgorithm, updateWeightAlgorithm,
  deleteWeightAlgorithm, toggleWeightAlgorithm, getWeightAlgorithmPreview,
  getWeightAlgorithmReferences,
  type WeightAlgorithm, type WeightAlgorithmParams, type WeightAlgorithmType,
  type WeightPreviewEntry, type HeatWeightPreviewEntry, type DelistWeightPreviewEntry,
  type WeightAlgorithmReference,
} from '@/api/weightAlgorithms'
import { getAccountDetails } from '@/api/accounts'
import type { AccountDetail } from '@/types'
import { PageLoading } from '@/components/common/Loading'
import { ConfirmModal } from '@/components/common/ConfirmModal'

const TYPE_LABELS: Record<WeightAlgorithmType, string> = {
  heat_weight: '热度加权',
  delist_weight: '下架加权',
}

interface ParamField {
  key: string
  label: string
  hint: string
  type: 'number' | 'bool' | 'select'
  options?: Array<{ value: string; label: string }>
}

const HEAT_PARAM_FIELDS: ParamField[] = [
  { key: 'first_use_bonus', label: '首次使用加成', hint: '从未发布过的素材加分（最高优先级）', type: 'number' },
  { key: 'recent_order_bonus', label: '近30天有订单加成', hint: '售卖情况好的编号加分', type: 'number' },
  { key: 'sold_bonus', label: '已售出加成', hint: '复销信号加分', type: 'number' },
  { key: 'offline_recover_per_day', label: '下架恢复速率', hint: '自动下架后每天恢复的分数（每天）', type: 'number' },
  { key: 'deleted_recover_per_day', label: '删除恢复速率', hint: '手动删除后每天恢复的分数（更慢）', type: 'number' },
  { key: 'fail_penalty', label: '失败扣分', hint: '近60天单次发布失败扣分', type: 'number' },
  { key: 'exclude_sold', label: '硬排已售出', hint: '开启后已售出编号不参与随机', type: 'bool' },
  {
    key: 'sample_mode',
    label: '选料方式',
    hint: '按权重直选：高分必先选；加权随机：高分仅概率高',
    type: 'select',
    options: [
      { value: 'weighted', label: '加权随机（概率与权重成正比）' },
      { value: 'top', label: '按权重直选（高分必先选）' },
    ],
  },
]

const DELIST_PARAM_FIELDS: ParamField[] = [
  { key: 'base_score', label: '基础分', hint: '所有在售商品的起始分', type: 'number' },
  { key: 'age_points_per_day', label: '上架老化加分', hint: '上架每多一天加的分（每天）', type: 'number' },
  { key: 'age_cap_days', label: '上架计分封顶', hint: '上架天数计分上限（天）', type: 'number' },
  { key: 'no_order_points_per_day', label: '无订单加分', hint: '无订单每多一天加的分（每天；无订单记录按上架天数计）', type: 'number' },
  { key: 'no_order_cap_days', label: '无单计分封顶', hint: '无订单天数计分上限（天）', type: 'number' },
  { key: 'recent_order_penalty', label: '近期订单扣分', hint: '近30天有订单的一次性扣分（保护）', type: 'number' },
  { key: 'polished_penalty', label: '擦亮扣分', hint: '已擦亮商品的扣分（保护近期活跃商品）', type: 'number' },
  { key: 'min_score', label: '下架分数线', hint: '权重低于该值不参与下架；0=不启用', type: 'number' },
  {
    key: 'sample_mode',
    label: '选取方式',
    hint: '按权重直选：高分必先下；加权随机：高分仅概率高',
    type: 'select',
    options: [
      { value: 'top', label: '按权重直选（高分必先下）' },
      { value: 'weighted', label: '加权随机（概率与权重成正比）' },
    ],
  },
  { key: 'exclude_recent_order', label: '硬排近期有单', hint: '开启后近30天有订单的商品不参与下架', type: 'bool' },
  { key: 'exclude_polished', label: '硬排已擦亮', hint: '开启后已擦亮的商品不参与下架', type: 'bool' },
]

/** 内置算法各类型可调参数键（其余参数只读） */
const BUILTIN_EDITABLE_KEYS: Record<WeightAlgorithmType, string[]> = {
  heat_weight: ['exclude_sold', 'sample_mode'],
  delist_weight: ['exclude_recent_order', 'exclude_polished', 'sample_mode'],
}

function defaultParamsFor(type: WeightAlgorithmType): Record<string, any> {
  if (type === 'delist_weight') {
    return {
      base_score: 100, age_points_per_day: 2, age_cap_days: 100,
      no_order_points_per_day: 8, no_order_cap_days: 30,
      recent_order_penalty: 120, polished_penalty: 60, min_score: 0,
      sample_mode: 'top', exclude_recent_order: false, exclude_polished: false,
    }
  }
  return {
    first_use_bonus: 50, recent_order_bonus: 30, sold_bonus: 25,
    offline_recover_per_day: 2, deleted_recover_per_day: 1, fail_penalty: 10,
    exclude_sold: false, sample_mode: 'weighted',
  }
}

function paramsSummary(type: WeightAlgorithmType, p: any): string {
  if (type === 'delist_weight') {
    return `老化+${p.age_points_per_day}/天(封顶${p.age_cap_days}) 无单+${p.no_order_points_per_day}/天(封顶${p.no_order_cap_days}) 有单-${p.recent_order_penalty} 擦亮-${p.polished_penalty}${p.min_score > 0 ? ` 线${p.min_score}` : ''}${p.exclude_recent_order ? ' 硬排有单' : ''}${p.exclude_polished ? ' 硬排擦亮' : ''} · ${p.sample_mode === 'top' ? '按权重直选' : '加权随机'}`
  }
  return `首次+${p.first_use_bonus} 订单+${p.recent_order_bonus} 售出+${p.sold_bonus} 恢复${p.offline_recover_per_day}/${p.deleted_recover_per_day}天${p.exclude_sold ? ' 硬排售出' : ''} · ${p.sample_mode === 'top' ? '按权重直选' : '加权随机'}`
}

function WeightAlgorithmsPage({ algorithmType }: { algorithmType: WeightAlgorithmType }) {
  const { addToast } = useUIStore()
  const [loading, setLoading] = useState(true)
  const [algorithms, setAlgorithms] = useState<WeightAlgorithm[]>([])
  const [showForm, setShowForm] = useState(false)
  const [editTarget, setEditTarget] = useState<WeightAlgorithm | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<WeightAlgorithm | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const [showList, setShowList] = useState(false) // 算法列表默认折叠
  const [previewTarget, setPreviewTarget] = useState<WeightAlgorithm | null>(null)
  const [referencesTarget, setReferencesTarget] = useState<WeightAlgorithm | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await getWeightAlgorithms()
      if (res.success) {
        setAlgorithms(res.data?.list ?? [])
      }
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    setLoading(true)
    load().finally(() => setLoading(false))
  }, [load])

  const filtered = algorithms.filter(a => a.algorithm_type === algorithmType)

  const handleToggle = async (a: WeightAlgorithm) => {
    try {
      const res = await toggleWeightAlgorithm(a.id)
      if (res.success) {
        addToast({ type: 'success', message: res.message || '操作成功' })
        load()
      } else {
        addToast({ type: 'error', message: res.message || '操作失败' })
      }
    } catch { addToast({ type: 'error', message: '操作失败' }) }
  }

  const handleDelete = async () => {
    if (!deleteConfirm) return
    setDeleting(true)
    try {
      const res = await deleteWeightAlgorithm(deleteConfirm.id)
      if (res.success) {
        addToast({ type: 'success', message: '算法已删除' })
        setDeleteConfirm(null)
        load()
      } else {
        addToast({ type: 'error', message: res.message || '删除失败' })
        setDeleteConfirm(null)
      }
    } catch { addToast({ type: 'error', message: '删除失败' }) }
    finally { setDeleting(false) }
  }

  if (loading) return <PageLoading />

  return (
    <div className="space-y-3 sm:space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="page-title">{algorithmType === 'heat_weight' ? '上架权重算法' : '下架权重算法'}</h1>
          <p className="page-description">
            {algorithmType === 'heat_weight'
              ? '管理定时发布随机模式的选料算法：按使用历史与售卖情况为素材打分，支持效果预览与引用查看'
              : '管理定时下架的选品算法：按上架天数、订单与擦亮情况为在售商品打分，支持效果预览与引用查看'}
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn-ios-secondary" onClick={() => load()}>
            <RefreshCw className="w-4 h-4" />刷新
          </button>
        </div>
      </div>

      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="vben-card">
        <div className="vben-card-header">
          <div className="flex items-center gap-2 cursor-pointer select-none" onClick={() => setShowList(v => !v)}>
            {showList
              ? <ChevronUp className="w-4 h-4 text-slate-400" />
              : <ChevronDown className="w-4 h-4 text-slate-400" />}
            <h2 className="vben-card-title">
              {algorithmType === 'heat_weight'
                ? <Scale className="w-4 h-4" />
                : <PackageX className="w-4 h-4" />}
              {TYPE_LABELS[algorithmType]}
            </h2>
            <span className="badge-primary">共 {filtered.length} 个</span>
          </div>
          <div className="flex items-center gap-2">
            <button className="btn-ios-secondary btn-sm" onClick={() => setShowHelp(true)}>
              <Info className="w-3.5 h-3.5" />算法说明
            </button>
            <button className="btn-ios-primary btn-sm" onClick={() => { setEditTarget(null); setShowForm(true) }}>
              <Plus className="w-4 h-4" />新建算法
            </button>
          </div>
        </div>

        {/* 算法列表（整体折叠，默认折叠） */}
        <AnimatePresence initial={false}>
          {showList && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="overflow-hidden"
            >
              <div className="vben-card-body border-t border-slate-200 dark:border-slate-700">
                {filtered.length === 0 ? (
                  <p className="text-center text-slate-400 py-10">暂无算法，点击「新建算法」开始</p>
                ) : (
                  <div className="space-y-2">
                    {filtered.map(a => (
                      <div key={a.id} className="flex flex-col sm:flex-row sm:items-center gap-2 p-3 rounded-xl border border-slate-200 dark:border-slate-700">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-medium text-slate-800 dark:text-slate-100">{a.name}</span>
                            {a.is_builtin && <span className="badge-primary">内置</span>}
                            <span className={a.enabled ? 'badge-success' : 'badge-gray'}>{a.enabled ? '启用' : '停用'}</span>
                            {a.ref_count > 0 && (
                              <button
                                className="badge-info cursor-pointer hover:opacity-80"
                                title="点击查看引用该算法的规则"
                                onClick={() => setReferencesTarget(a)}
                              >
                                被 {a.ref_count} 条规则引用
                              </button>
                            )}
                          </div>
                          <p className="text-xs text-slate-500 mt-1 font-mono">{paramsSummary(a.algorithm_type, a.params)}</p>
                          {a.description && <p className="text-sm text-slate-500 mt-0.5 truncate">{a.description}</p>}
                        </div>
                        {/* 内置算法同样展示三个按钮（保持行统一）：编辑可点击（弹窗内仅硬排开关/选取方式可调），停用/删除禁用 */}
                        <div className="flex gap-2 flex-shrink-0">
                          <button
                            className="btn-ios-secondary btn-sm"
                            title="查看算法效果（按当前数据计算权重）"
                            onClick={() => setPreviewTarget(a)}
                          >
                            <BarChart3 className="w-3.5 h-3.5" />效果
                          </button>
                          <button
                            className="btn-ios-secondary btn-sm"
                            title={a.is_builtin ? '内置算法仅可调整硬排开关与选取方式' : '编辑'}
                            onClick={() => { setEditTarget(a); setShowForm(true) }}
                          >
                            <Pencil className="w-3.5 h-3.5" />编辑
                          </button>
                          <button
                            className="btn-ios-secondary btn-sm disabled:opacity-50 disabled:cursor-not-allowed"
                            disabled={a.is_builtin}
                            title={a.is_builtin ? '系统内置算法不可停用' : (a.enabled ? '停用' : '启用')}
                            onClick={() => handleToggle(a)}
                          >
                            {a.enabled ? <PowerOff className="w-3.5 h-3.5" /> : <Power className="w-3.5 h-3.5" />}
                          </button>
                          <button
                            className="btn-ios-danger btn-sm disabled:opacity-50 disabled:cursor-not-allowed"
                            disabled={a.is_builtin}
                            title={a.is_builtin ? '系统内置算法不可删除' : '删除'}
                            onClick={() => setDeleteConfirm(a)}
                          >
                            <Trash2 className="w-3.5 h-3.5" />删除
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>

      {/* 算法说明弹窗（按类型） */}
      <AnimatePresence>
        {showHelp && (
          <div className="modal-overlay" onClick={() => setShowHelp(false)}>
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }}
              className="modal-content max-w-xl max-h-[85vh] flex flex-col"
              onClick={e => e.stopPropagation()}
            >
              <div className="modal-header">
                <h2 className="modal-title flex items-center gap-2">
                  <Info className="w-5 h-5" />{TYPE_LABELS[algorithmType]}算法说明
                </h2>
                <button className="modal-close" onClick={() => setShowHelp(false)}><X className="w-5 h-5" /></button>
              </div>
              <div className="modal-body overflow-y-auto space-y-4 text-sm text-slate-600 dark:text-slate-300">
                {algorithmType === 'heat_weight' ? (
                  <>
                    <p>
                      「热度加权」用于定时发布随机模式：按使用历史与售卖情况为每个素材打分，选料时<b>权重越高越优先</b>，可选「按权重直选」或「加权随机」两种选料方式。素材池永不枯竭（保底 1 分），被下架/删除的素材会随时间逐步恢复权重。
                    </p>

                    <div>
                      <h3 className="text-xs font-semibold text-slate-400 mb-1.5">权重公式</h3>
                      <div className="rounded-lg bg-slate-50 dark:bg-slate-800 p-3 font-mono text-xs leading-relaxed">
                        权重 = 100 基础分<br />
                        &nbsp;&nbsp;+ 首次使用加成（从未发布过）<br />
                        &nbsp;&nbsp;+ 近30天有订单加成<br />
                        &nbsp;&nbsp;+ 已售出加成（复销信号）<br />
                        &nbsp;&nbsp;− 下架恢复惩罚（每天恢复，50 天回满）<br />
                        &nbsp;&nbsp;− 删除恢复惩罚（每天恢复，100 天回满）<br />
                        &nbsp;&nbsp;− 失败扣分 × 近60天失败次数<br />
                        &nbsp;&nbsp;保底 1 分
                      </div>
                    </div>

                    <div>
                      <h3 className="text-xs font-semibold text-slate-400 mb-1.5">参数说明</h3>
                      <div className="space-y-1.5">
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">首次使用加成</span>从未出现在商品记录/发布日志/订单任何一处的素材加分（最高优先级）</p>
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">近30天订单加成</span>近期有订单的编号优先</p>
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">已售出加成</span>卖掉的款式重新上架是合理的复销行为</p>
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">下架/删除恢复速率</span>退场素材每天恢复的分数，控制「多久后可以重新被选中」</p>
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">失败扣分</span>减少容易发布失败的素材被选中的概率</p>
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">硬排已售出</span>开启后已售出编号完全不参与随机（硬排除）</p>
                      </div>
                    </div>

                    <div>
                      <h3 className="text-xs font-semibold text-slate-400 mb-1.5">选料方式</h3>
                      <div className="space-y-1.5">
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">加权随机</span>被选中概率与权重成正比：权重高 = 概率高，不保证必选</p>
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">按权重直选</span>按权重从高到低依次选取，高分必先选；补发轮同样按权重顺序</p>
                      </div>
                    </div>

                    <div>
                      <h3 className="text-xs font-semibold text-slate-400 mb-1.5">执行顺序</h3>
                      <p className="text-xs leading-relaxed">
                        先硬过滤（去重：编号已在规则账号在售的素材 → 可选：硬排已售出），再对剩余素材按选料方式选取。
                      </p>
                    </div>
                  </>
                ) : (
                  <>
                    <p>
                      「下架加权」用于定时下架：按上架天数、订单与擦亮情况为每个在售商品打分，选品时<b>权重越高越先下架</b>，可选「按权重直选」或「加权随机」两种选取方式。仅「在售」状态商品参与（非在售下架接口必然失败）。
                    </p>

                    <div>
                      <h3 className="text-xs font-semibold text-slate-400 mb-1.5">权重公式</h3>
                      <div className="rounded-lg bg-slate-50 dark:bg-slate-800 p-3 font-mono text-xs leading-relaxed">
                        权重 = 基础分<br />
                        &nbsp;&nbsp;+ min(上架天数, 封顶) × 上架老化加分/天<br />
                        &nbsp;&nbsp;+ min(无订单天数, 封顶) × 无订单加分/天<br />
                        &nbsp;&nbsp;− 近期订单扣分（近30天有订单）<br />
                        &nbsp;&nbsp;− 擦亮扣分（已擦亮）<br />
                        &nbsp;&nbsp;下限 0 分（0 = 不参与下架）
                      </div>
                    </div>

                    <div>
                      <h3 className="text-xs font-semibold text-slate-400 mb-1.5">参数说明</h3>
                      <div className="space-y-1.5">
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">上架老化加分</span>上架越久越该下，每天累计，封顶后不再增长</p>
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">无订单加分</span>无订单越久越该下（无订单天数 = 最近订单距今；从未有单按上架天数计）</p>
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">近期订单扣分</span>近30天有订单的商品一次性扣分（保护近期有单商品）</p>
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">擦亮扣分</span>已擦亮 = 近期主动操作，扣分保护</p>
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">下架分数线</span>权重低于该值的商品不参与下架；0 = 不启用</p>
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">硬排近期有单/已擦亮</span>开启后对应商品完全不参与下架（硬排除）</p>
                      </div>
                    </div>

                    <div>
                      <h3 className="text-xs font-semibold text-slate-400 mb-1.5">选取方式</h3>
                      <div className="space-y-1.5">
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">按权重直选</span>按权重从高到低依次选取，高分必先下</p>
                        <p><span className="inline-block min-w-[108px] font-medium text-slate-700 dark:text-slate-200">加权随机</span>被选中概率与权重成正比：权重高 = 概率高，不保证必下</p>
                      </div>
                    </div>

                    <div>
                      <h3 className="text-xs font-semibold text-slate-400 mb-1.5">执行顺序</h3>
                      <p className="text-xs leading-relaxed">
                        仅规则内账号的在售商品参与；先硬排（可选：硬排近期有单/已擦亮），再按选取方式每账号取不超过上限 Z 个。
                      </p>
                    </div>
                  </>
                )}

                <p className="text-xs text-slate-400 border-t border-slate-200 dark:border-slate-700 pt-2.5">
                  规则未选择算法时使用系统默认参数。算法被停用后，引用它的规则自动回退默认参数；内置算法仅硬排开关与选取方式可调。
                </p>
              </div>
              <div className="modal-footer flex-shrink-0">
                <button className="btn-ios-primary" onClick={() => setShowHelp(false)}>知道了</button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* 新建/编辑弹窗 */}
      <AnimatePresence>
        {showForm && (
          <WeightAlgorithmFormModal
            initial={editTarget}
            defaultType={algorithmType}
            onClose={() => { setShowForm(false); setEditTarget(null) }}
            onSaved={() => { setShowForm(false); setEditTarget(null); load() }}
          />
        )}
      </AnimatePresence>

      <ConfirmModal
        isOpen={!!deleteConfirm}
        title="确认删除"
        message={`确认删除权重算法「${deleteConfirm?.name ?? ''}」？被定时发布/定时下架规则引用的算法无法删除。`}
        confirmText="删除"
        type="danger"
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => setDeleteConfirm(null)}
      />

      {/* 算法效果预览 */}
      {previewTarget && (
        <WeightAlgorithmPreviewModal
          initial={previewTarget}
          onClose={() => setPreviewTarget(null)}
        />
      )}

      {/* 引用规则列表 */}
      {referencesTarget && (
        <WeightAlgorithmReferencesModal
          initial={referencesTarget}
          onClose={() => setReferencesTarget(null)}
        />
      )}
    </div>
  )
}

function WeightAlgorithmFormModal({ initial, defaultType, onClose, onSaved }: {
  initial: WeightAlgorithm | null
  defaultType: WeightAlgorithmType
  onClose: () => void
  onSaved: () => void
}) {
  const { addToast } = useUIStore()
  // 内置算法：仅硬排开关与选取方式可调，其余字段与参数只读
  const isBuiltin = !!initial?.is_builtin
  // 算法类型由页面入口决定（编辑时以算法实际类型为准）
  const algorithmType: WeightAlgorithmType = initial?.algorithm_type || defaultType
  const [loading, setLoading] = useState(false)
  const [name, setName] = useState(initial?.name || '')
  const [description, setDescription] = useState(initial?.description || '')
  const [params, setParams] = useState<Record<string, any>>(() => ({
    ...(initial?.params ?? defaultParamsFor(algorithmType)),
  }))

  const isBuiltinEditable = (key: string) =>
    isBuiltin && !BUILTIN_EDITABLE_KEYS[algorithmType].includes(key)

  const handleSave = async () => {
    if (!name.trim()) { addToast({ type: 'warning', message: '请输入算法名称' }); return }
    setLoading(true)
    try {
      const payload = {
        name: name.trim(),
        algorithm_type: algorithmType,
        description: description.trim() || undefined,
        params: params as WeightAlgorithmParams,
      }
      const res = initial
        ? await updateWeightAlgorithm(initial.id, payload)
        : await createWeightAlgorithm(payload)
      if (res.success) {
        addToast({ type: 'success', message: initial ? '算法已更新' : '算法创建成功' })
        onSaved()
      } else {
        addToast({ type: 'error', message: res.message || '保存失败' })
      }
    } catch {
      addToast({ type: 'error', message: '操作失败，请重试' })
    } finally {
      setLoading(false)
    }
  }

  const fields = algorithmType === 'delist_weight' ? DELIST_PARAM_FIELDS : HEAT_PARAM_FIELDS

  return (
    <div className="modal-overlay" onClick={onClose}>
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }}
        className="modal-content max-w-2xl max-h-[90vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 className="modal-title flex items-center gap-2">
            {algorithmType === 'heat_weight' ? <Scale className="w-5 h-5" /> : <PackageX className="w-5 h-5" />}
            {initial ? '编辑权重算法' : '新建权重算法'}
          </h2>
          <button className="modal-close" onClick={onClose}><X className="w-5 h-5" /></button>
        </div>
        <div className="modal-body overflow-y-auto space-y-4">
          {isBuiltin && (
            <p className="text-xs text-amber-600 dark:text-amber-400 rounded-lg bg-amber-50 dark:bg-amber-900/20 px-3 py-2">
              系统内置算法：仅硬排开关与选取方式可调整，其余参数只读。
            </p>
          )}
          <div className="input-group">
            <label className="input-label">算法名称 <span className="text-red-500">*</span></label>
            <input className="input-ios" placeholder={algorithmType === 'delist_weight' ? '如：下架均衡' : '如：热度均衡'} value={name}
              onChange={e => setName(e.target.value)} maxLength={100} disabled={isBuiltin} />
          </div>
          <div className="input-group">
            <label className="input-label">算法说明</label>
            <input className="input-ios" placeholder="一句话描述策略（可选）" value={description}
              onChange={e => setDescription(e.target.value)} maxLength={500} disabled={isBuiltin} />
          </div>
          <div className="vben-card">
            <div className="vben-card-header">
              <h3 className="vben-card-title text-sm">权重参数</h3>
            </div>
            <div className="vben-card-body grid grid-cols-1 sm:grid-cols-2 gap-3">
              {fields.map(f => (
                <div key={f.key} className="input-group">
                  <label className="input-label">{f.label}</label>
                  {f.type === 'select' ? (
                    <select className="input-ios" value={params[f.key] as string}
                      disabled={isBuiltinEditable(f.key)}
                      onChange={e => setParams(p => ({ ...p, [f.key]: e.target.value }))}>
                      {(f.options ?? []).map(o => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  ) : f.type === 'bool' ? (
                    <label className="switch-ios mt-1">
                      <input type="checkbox" checked={params[f.key] as boolean}
                        disabled={isBuiltinEditable(f.key)}
                        onChange={e => setParams(p => ({ ...p, [f.key]: e.target.checked }))} />
                      <span className="switch-slider"></span>
                    </label>
                  ) : (
                    <input type="number" className="input-ios" value={params[f.key] as number}
                      disabled={isBuiltinEditable(f.key)}
                      onChange={e => setParams(p => ({ ...p, [f.key]: Number(e.target.value) || 0 }))} />
                  )}
                  <p className="text-xs text-slate-400 mt-0.5">{f.hint}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="modal-footer flex-shrink-0">
          <button className="btn-ios-secondary" onClick={onClose}>取消</button>
          <button className="btn-ios-primary" disabled={loading} onClick={handleSave}>
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            {initial ? '保存修改' : '创建算法'}
          </button>
        </div>
      </motion.div>
    </div>
  )
}

function WeightAlgorithmPreviewModal({ initial, onClose }: {
  initial: WeightAlgorithm
  onClose: () => void
}) {
  const { addToast } = useUIStore()
  const [loading, setLoading] = useState(true)
  const [entries, setEntries] = useState<WeightPreviewEntry[]>([])
  const [total, setTotal] = useState(0)
  const [emptyMessage, setEmptyMessage] = useState('')
  // 下架预览的账号范围：空集合 = 全部账号
  const [accounts, setAccounts] = useState<AccountDetail[]>([])
  const [selectedAccounts, setSelectedAccounts] = useState<Set<string>>(new Set())
  // 预览前先同步闲鱼最新商品（本地快照可能落后于闲鱼）
  const [refreshBeforePreview, setRefreshBeforePreview] = useState(true)

  const isDelist = initial.algorithm_type === 'delist_weight'

  useEffect(() => {
    if (!isDelist) return
    getAccountDetails()
      .then(list => setAccounts(list))
      .catch(() => {})
  }, [isDelist])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const accountIds = isDelist && selectedAccounts.size > 0
      ? Array.from(selectedAccounts)
      : undefined
    getWeightAlgorithmPreview(
      initial.id,
      accountIds,
      isDelist && refreshBeforePreview,
    )
      .then(res => {
        if (cancelled) return
        if (res.success) {
          setEntries(res.data?.list ?? [])
          setTotal(res.data?.total ?? 0)
          setEmptyMessage(res.message || '')
        } else {
          addToast({ type: 'error', message: res.message || '预览失败' })
        }
      })
      .catch(() => {
        if (!cancelled) addToast({ type: 'error', message: '预览失败，请稍后重试' })
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [initial.id, isDelist, selectedAccounts, refreshBeforePreview, addToast])

  const toggleAccount = (id: string) => {
    setSelectedAccounts(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }}
        className="modal-content max-w-2xl max-h-[85vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 className="modal-title flex items-center gap-2 flex-wrap">
            {isDelist ? <PackageX className="w-5 h-5" /> : <BarChart3 className="w-5 h-5" />}
            {initial.name} · 效果预览
            <span className="badge-gray text-xs">
              {initial.params.sample_mode === 'top' ? '按权重直选' : '加权随机'}
            </span>
          </h2>
          <button className="modal-close" onClick={onClose}><X className="w-5 h-5" /></button>
        </div>
        <div className="modal-body overflow-y-auto space-y-3 text-sm text-slate-600 dark:text-slate-300">
          {isDelist ? (
            <>
              {/* 账号范围选择：全部账号 / 指定账号 */}
              <div className="border border-slate-200 dark:border-slate-700 rounded-xl p-2.5 space-y-1.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium text-slate-500 dark:text-slate-400">
                    预览账号范围{selectedAccounts.size === 0 ? '：全部账号' : `：已选 ${selectedAccounts.size} 个`}
                  </span>
                  {selectedAccounts.size > 0 && (
                    <button className="text-xs text-blue-500 hover:underline" onClick={() => setSelectedAccounts(new Set())}>
                      改为全部账号
                    </button>
                  )}
                </div>
                <div className="space-y-0.5 max-h-32 overflow-y-auto">
                  {accounts.length === 0 ? (
                    <p className="text-xs text-slate-400 px-2 py-1">暂无账号</p>
                  ) : (
                    accounts.map(a => (
                      <label key={a.id} className={`flex items-center gap-2 px-2 py-1 rounded-lg cursor-pointer transition-colors ${selectedAccounts.has(a.id) ? 'bg-blue-50 dark:bg-blue-900/20' : 'hover:bg-slate-50 dark:hover:bg-slate-700'}`}>
                        <input type="checkbox" className="w-4 h-4 text-blue-600 rounded"
                          checked={selectedAccounts.has(a.id)} onChange={() => toggleAccount(a.id)} />
                        <span className="text-sm truncate text-slate-700 dark:text-slate-200">{a.note || a.id}</span>
                      </label>
                    ))
                  )}
                </div>
                <label className="flex items-center gap-2 pt-1.5 border-t border-slate-100 dark:border-slate-800 cursor-pointer">
                  <input type="checkbox" className="w-4 h-4 text-blue-600 rounded"
                    checked={refreshBeforePreview} onChange={() => setRefreshBeforePreview(v => !v)} />
                  <span className="text-xs text-slate-500 dark:text-slate-400">
                    预览前先从闲鱼同步最新商品（商品较多时较慢；同步失败自动回退本地数据）
                  </span>
                </label>
              </div>
              <p className="text-xs text-slate-400">
                预览范围：{selectedAccounts.size === 0 ? '全部账号' : `已选 ${selectedAccounts.size} 个账号`}，
                共 {total} 条在售商品，按权重降序排列；权重越高越先被下架。每行下方为该商品的逐项分值构成。
              </p>
            </>
          ) : (() => {
            const filteredCount = entries
              .filter(e => 'on_sale_filtered' in e && e.on_sale_filtered).length
            return (
              <p className="text-xs text-slate-400">
                对当前账号全部 {total} 条素材计算权重，降序排列；权重越高越容易被随机选中。
                {filteredCount > 0 && (
                  <>其中 <b>{filteredCount}</b> 条编号本地状态为在售，执行去重时会被硬过滤（行标灰）。</>
                )}
                每行下方为该素材的逐项分值构成。
              </p>
            )
          })()}
          {loading ? (
            <div className="flex flex-col items-center justify-center gap-2 py-16">
              <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
              {isDelist && refreshBeforePreview && (
                <p className="text-xs text-slate-400">正在从闲鱼同步商品并计算权重，商品较多时可能需要几十秒…</p>
              )}
            </div>
          ) : entries.length === 0 ? (
            <p className="text-center text-slate-400 py-10">{emptyMessage || '暂无数据，无法预览'}</p>
          ) : isDelist ? (
            <div className="space-y-1.5">
              {entries.map((raw, i) => {
                const e = raw as DelistWeightPreviewEntry
                const p = e.parts
                const fmt = (v: number) => (v > 0 ? `+${v}` : String(v))
                return (
                  <div key={e.item_id} className="p-2.5 rounded-xl border border-slate-200 dark:border-slate-700">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs text-slate-400 w-6 text-right font-mono shrink-0">{i + 1}</span>
                      {e.account_id && <span className="badge-gray font-mono shrink-0">{e.account_id}</span>}
                      <span className="flex-1 min-w-[120px] truncate text-slate-700 dark:text-slate-200" title={e.title}>{e.title}</span>
                      <span className="badge-info font-mono shrink-0">权重 {e.weight}</span>
                      <span className="flex gap-1 flex-wrap shrink-0">
                        <span className="badge-gray">上架{e.signals.age_days}天</span>
                        <span className="badge-gray">无单{e.signals.no_order_days}天</span>
                        {e.signals.recent_order && <span className="badge-success">近30天有单</span>}
                        {e.signals.polished && <span className="badge-primary">已擦亮</span>}
                      </span>
                    </div>
                    {/* 逐项分值构成 */}
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-1.5 pl-8 font-mono text-xs text-slate-500 dark:text-slate-400">
                      <span>基础 {p.base}</span>
                      <span>老化 {fmt(p.age_points)}</span>
                      <span>无单 {fmt(p.no_order_points)}</span>
                      <span>有单 {fmt(p.recent_order_penalty)}</span>
                      <span>擦亮 {fmt(p.polished_penalty)}</span>
                      <span className="text-slate-700 dark:text-slate-200 font-semibold">= 合计 {e.weight}{e.clamped ? '（保底0）' : ''}</span>
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="space-y-1.5">
              {entries.map((raw, i) => {
                const e = raw as HeatWeightPreviewEntry
                const p = e.parts
                const fmt = (v: number) => (v > 0 ? `+${v}` : String(v))
                return (
                  <div key={e.material_id} className={`p-2.5 rounded-xl border border-slate-200 dark:border-slate-700 ${e.on_sale_filtered ? 'opacity-60' : ''}`}>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs text-slate-400 w-6 text-right font-mono shrink-0">{i + 1}</span>
                      <span className="badge-primary font-mono shrink-0">{e.item_no != null ? `A${e.item_no}` : '无编号'}</span>
                      <span className="flex-1 min-w-[120px] truncate text-slate-700 dark:text-slate-200" title={e.title}>{e.title}</span>
                      <span className="badge-info font-mono shrink-0">权重 {e.weight}</span>
                      {e.on_sale_filtered && <span className="badge-gray shrink-0">在售（去重过滤）</span>}
                      <span className="flex gap-1 flex-wrap shrink-0">
                        {e.signals.first_use && <span className="badge-success">首次使用</span>}
                        {e.signals.recent_order && <span className="badge-info">近30天订单</span>}
                        {e.signals.sold && <span className="badge-primary">已售出</span>}
                        {e.signals.offline_days != null && <span className="badge-gray">下架{e.signals.offline_days}天</span>}
                        {e.signals.deleted_days != null && <span className="badge-gray">删除{e.signals.deleted_days}天</span>}
                        {e.signals.fail_count > 0 && <span className="badge-gray">失败×{e.signals.fail_count}</span>}
                      </span>
                    </div>
                    {/* 逐项分值构成 */}
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-1.5 pl-8 font-mono text-xs text-slate-500 dark:text-slate-400">
                      <span>基础 {p.base}</span>
                      <span>首次使用 {fmt(p.first_use_bonus)}</span>
                      <span>近30天订单 {fmt(p.recent_order_bonus)}</span>
                      <span>已售出 {fmt(p.sold_bonus)}</span>
                      <span>下架 {fmt(p.offline_penalty)}</span>
                      <span>删除 {fmt(p.deleted_penalty)}</span>
                      <span>失败 {fmt(p.fail_penalty)}</span>
                      <span className="text-slate-700 dark:text-slate-200 font-semibold">= 合计 {e.weight}{e.clamped ? '（保底1）' : ''}</span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
        <div className="modal-footer flex-shrink-0">
          <button className="btn-ios-primary" onClick={onClose}>关闭</button>
        </div>
      </motion.div>
    </div>
  )
}

function WeightAlgorithmReferencesModal({ initial, onClose }: {
  initial: WeightAlgorithm
  onClose: () => void
}) {
  const { addToast } = useUIStore()
  const [loading, setLoading] = useState(true)
  const [references, setReferences] = useState<WeightAlgorithmReference[]>([])
  const isDelist = initial.algorithm_type === 'delist_weight'

  useEffect(() => {
    let cancelled = false
    getWeightAlgorithmReferences(initial.id)
      .then(res => {
        if (cancelled) return
        if (res.success) {
          setReferences(res.data?.list ?? [])
        } else {
          addToast({ type: 'error', message: res.message || '查询失败' })
        }
      })
      .catch(() => {
        if (!cancelled) addToast({ type: 'error', message: '查询失败，请稍后重试' })
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [initial.id, addToast])

  return (
    <div className="modal-overlay" onClick={onClose}>
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }}
        className="modal-content max-w-xl max-h-[80vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 className="modal-title flex items-center gap-2">
            {isDelist ? <PackageX className="w-5 h-5" /> : <Scale className="w-5 h-5" />}
            引用「{initial.name}」的规则
          </h2>
          <button className="modal-close" onClick={onClose}><X className="w-5 h-5" /></button>
        </div>
        <div className="modal-body overflow-y-auto space-y-1.5 text-sm text-slate-600 dark:text-slate-300">
          {loading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
            </div>
          ) : references.length === 0 ? (
            <p className="text-center text-slate-400 py-10">暂无规则引用该算法</p>
          ) : (
            references.map(r => (
              <div key={r.id} className="flex flex-wrap items-center gap-2 p-3 rounded-xl border border-slate-200 dark:border-slate-700">
                <span className="flex-1 min-w-[120px] font-medium text-slate-800 dark:text-slate-100">{r.name}</span>
                <span className="badge-gray">#{r.id}</span>
                {isDelist ? (
                  <span className="badge-primary">每次最多 {r.max_count ?? 0} 个</span>
                ) : (
                  <span className={r.publish_mode === 'random' ? 'badge-primary' : 'badge-gray'}>
                    {r.publish_mode === 'random' ? `随机 ${r.random_count ?? 0} 条` : '指定发布'}
                  </span>
                )}
                <span className={r.enabled ? 'badge-success' : 'badge-gray'}>{r.enabled ? '启用' : '停用'}</span>
                {r.next_trigger_at && (
                  <span className="text-xs text-slate-400 font-mono">下次触发 {r.next_trigger_at}</span>
                )}
              </div>
            ))
          )}
        </div>
        <div className="modal-footer flex-shrink-0">
          <button className="btn-ios-primary" onClick={onClose}>关闭</button>
        </div>
      </motion.div>
    </div>
  )
}

/** 上架权重算法页（优化算法 → 上架权重算法） */
export function HeatWeightAlgorithms() {
  return <WeightAlgorithmsPage algorithmType="heat_weight" />
}

/** 下架权重算法页（优化算法 → 下架权重算法） */
export function DelistWeightAlgorithms() {
  return <WeightAlgorithmsPage algorithmType="delist_weight" />
}

export default HeatWeightAlgorithms
