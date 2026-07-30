/**
 * 批量导入素材弹窗
 *
 * 功能：
 * 1. 输入本地素材目录路径
 * 2. 扫描目录，解析 txt + 图片
 * 3. 预览扫描结果，统一设置字段，也可逐条单独调整
 * 4. 确认后批量导入到素材库
 */
import { useState } from 'react'
import { X, Loader2, Search, FolderOpen, Image, ChevronDown, ChevronUp, Pencil } from 'lucide-react'
import { useUIStore } from '@/store/uiStore'
import {
  scanDirectory,
  batchImportMaterials,
  type ScannedMaterial,
  type BatchImportParams,
} from '@/api/productPublish'

const CATEGORIES = ['数码家电', '服饰鞋包', '家居日用', '图书音像', '美妆个护', '母婴用品', '运动户外', '食品生鲜', '虚拟商品', '其他']
const CONDITIONS = ['全新', '99新', '95新', '9成新', '8成新', '7成新以下']

interface Props {
  onClose: () => void
  onImported: () => void
}

/** 单条素材的可编辑字段 */
interface ItemSettings {
  price: string
  category: string
  condition: string
  brand: string
  delivery_method: 'express' | 'pickup'
  postage: string
}

/** 构建一条素材的最终设置（逐条覆盖统一默认值） */
function buildItemSettings(defaults: ItemSettings, overrides: Partial<ItemSettings>): ItemSettings {
  return {
    price: overrides.price ?? defaults.price,
    category: overrides.category ?? defaults.category,
    condition: overrides.condition ?? defaults.condition,
    brand: overrides.brand ?? defaults.brand,
    delivery_method: overrides.delivery_method ?? defaults.delivery_method,
    postage: overrides.postage ?? defaults.postage,
  }
}

export function BatchImportModal({ onClose, onImported }: Props) {
  const { addToast } = useUIStore()

  // 路径输入
  const [dirPath, setDirPath] = useState('')

  // 扫描状态
  const [scanning, setScanning] = useState(false)
  const [materials, setMaterials] = useState<ScannedMaterial[]>([])
  const [scanned, setScanned] = useState(false)

  // 选择状态
  const [selectedCodes, setSelectedCodes] = useState<Set<string>>(new Set())

  // 展开单条编辑的素材编号
  const [expandedCode, setExpandedCode] = useState<string | null>(null)

  // 统一字段默认值
  const [defaults, setDefaults] = useState<ItemSettings>({
    price: '9.9',
    category: '虚拟商品',
    condition: '全新',
    brand: '',
    delivery_method: 'express',
    postage: '0',
  })

  // 逐条覆盖值：{ code: Partial<ItemSettings> }
  const [overrides, setOverrides] = useState<Record<string, Partial<ItemSettings>>>({})

  // 编号插入位置：none | title | description | both
  const [codeInsertMode, setCodeInsertMode] = useState<'none' | 'title' | 'description' | 'both'>('none')

  // 导入状态
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<{
    imported: number
    failed: number
    failed_items: { code: string; reason: string }[]
  } | null>(null)

  /** 获取某条素材的最终设置 */
  const getSettings = (code: string): ItemSettings =>
    buildItemSettings(defaults, overrides[code] || {})

  /** 更新某条素材的单个字段 */
  const updateOverride = (code: string, field: keyof ItemSettings, value: string) => {
    setOverrides(prev => {
      const current = prev[code] || {}
      const updated = { ...current, [field]: value }
      // 跟默认值一样就不算覆盖
      if (updated[field] === defaults[field]) {
        const { [field]: _, ...rest } = updated
        return Object.keys(rest).length > 0
          ? { ...prev, [code]: rest }
          : Object.fromEntries(Object.entries(prev).filter(([k]) => k !== code))
      }
      return { ...prev, [code]: updated }
    })
  }

  /** 把统一默认值应用到全部选中素材（清除所有覆盖） */
  const applyDefaultsToAll = () => {
    setOverrides({})
    addToast({ type: 'success', message: '统一设置已应用到全部素材' })
  }

  /** 扫描目录 */
  const handleScan = async () => {
    if (!dirPath.trim()) {
      addToast({ type: 'warning', message: '请输入素材目录路径' })
      return
    }
    setScanning(true)
    setScanned(false)
    setMaterials([])
    setSelectedCodes(new Set())
    setOverrides({})
    setExpandedCode(null)
    setImportResult(null)
    try {
      const res = await scanDirectory(dirPath.trim())
      if (res.success && res.data) {
        setMaterials(res.data.materials)
        setSelectedCodes(new Set(res.data.materials.map(m => m.code)))
        setScanned(true)
        addToast({ type: 'success', message: `扫描完成，发现 ${res.data.total} 个素材` })
      } else {
        addToast({ type: 'error', message: res.message || '扫描失败' })
      }
    } catch {
      addToast({ type: 'error', message: '扫描失败，请检查路径是否正确' })
    } finally {
      setScanning(false)
    }
  }

  /** 全选/取消全选 */
  const toggleSelectAll = () => {
    if (materials.length === 0) return
    if (selectedCodes.size === materials.length) {
      setSelectedCodes(new Set())
    } else {
      setSelectedCodes(new Set(materials.map(m => m.code)))
    }
  }

  /** 切换单条选中 */
  const toggleSelect = (code: string) => {
    setSelectedCodes(prev => {
      const next = new Set(prev)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return next
    })
  }

  /** 执行导入 */
  const handleImport = async () => {
    const selected = materials.filter(m => selectedCodes.has(m.code))
    if (selected.length === 0) {
      addToast({ type: 'warning', message: '请至少选择一条素材' })
      return
    }
    setImporting(true)
    setImportResult(null)
    try {
      const payload: BatchImportParams = {
        materials: selected.map(m => {
          const s = getSettings(m.code)
          return {
            code: m.code,
            folder_name: m.folder_name,
            title: (() => {
              const t = m.title
              if (codeInsertMode === 'title' || codeInsertMode === 'both') {
                return `${m.code} ${t}`
              }
              return t
            })(),
            description: (() => {
              const d = m.description
              if (codeInsertMode === 'description' || codeInsertMode === 'both') {
                return `${d}\n\n${m.code}`
              }
              return d
            })(),
            images: m.images,
            price: parseFloat(s.price) || 0,
            category: s.category,
            condition: s.condition,
            brand: s.brand.trim(),
            delivery_method: s.delivery_method,
            postage: parseFloat(s.postage) || 0,
          }
        }),
      }
      const res = await batchImportMaterials(payload)
      if (res.success && res.data) {
        setImportResult(res.data)
        if (res.data.failed === 0) {
          addToast({ type: 'success', message: `成功导入 ${res.data.imported} 条素材！` })
          setTimeout(() => onImported(), 1200)
        } else {
          addToast({
            type: 'warning',
            message: `导入完成：成功 ${res.data.imported} 条，失败 ${res.data.failed} 条`,
          })
        }
      } else {
        addToast({ type: 'error', message: res.message || '导入失败' })
      }
    } catch {
      addToast({ type: 'error', message: '导入失败，请重试' })
    } finally {
      setImporting(false)
    }
  }

  const selectedCount = selectedCodes.size
  const allSelected = materials.length > 0 && selectedCount === materials.length
  const noneSelected = selectedCount === 0

  return (
    <div className="modal-overlay">
      <div className="modal-content max-w-3xl max-h-[90vh] flex flex-col">
        {/* 标题栏 */}
        <div className="modal-header flex-shrink-0">
          <h2 className="modal-title flex items-center gap-2">
            <FolderOpen className="w-5 h-5" />批量导入素材
          </h2>
          <button className="modal-close" onClick={onClose} disabled={importing}>
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* 内容区（可滚动） */}
        <div className="modal-body flex-1 overflow-y-auto">
          <div className="space-y-3">
            {/* 路径输入 */}
            <div className="input-group">
              <label className="input-label">素材目录路径</label>
              <div className="flex gap-2">
                <input
                  className="input-ios flex-1 font-mono text-sm"
                  placeholder="例如：D:\素材库\商品素材"
                  value={dirPath}
                  onChange={e => setDirPath(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleScan()}
                  disabled={scanning || importing}
                />
                <button
                  className="btn-ios-primary"
                  onClick={handleScan}
                  disabled={scanning || importing}
                >
                  {scanning ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Search className="w-4 h-4" />
                  )}
                  扫描
                </button>
              </div>
              <p className="text-xs text-slate-400 mt-1">
                目录下每个子文件夹为一个素材，包含 .txt 元数据和 .jpg 图片
              </p>
            </div>

            {/* 扫描中 */}
            {scanning && (
              <div className="flex items-center justify-center py-12 text-slate-400">
                <Loader2 className="w-8 h-8 animate-spin text-blue-500 mr-3" />
                <span>正在扫描目录...</span>
              </div>
            )}

            {/* 扫描结果 */}
            {scanned && !scanning && (
              <>
                {/* ===== 统一设置区 ===== */}
                <div className="vben-card">
                  <div className="vben-card-header py-2.5 px-4">
                    <p className="vben-card-title text-sm">统一设置</p>
                    <button
                      className="text-xs text-blue-500 hover:text-blue-600 transition-colors font-medium"
                      onClick={applyDefaultsToAll}
                      disabled={importing}
                    >
                      应用到全部
                    </button>
                  </div>
                  <div className="vben-card-body py-2 px-4">
                    <p className="text-xs text-slate-400 mb-2">
                      所有素材的默认值，可点击右侧「应用到全部」清除逐条设置
                    </p>
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                      <div className="input-group">
                        <label className="input-label text-xs">售价（元）</label>
                        <input
                          type="number"
                          className="input-ios text-sm"
                          min="0" step="0.01"
                          value={defaults.price}
                          onChange={e => setDefaults(d => ({ ...d, price: e.target.value }))}
                          disabled={importing}
                        />
                      </div>
                      <div className="input-group">
                        <label className="input-label text-xs">分类</label>
                        <select
                          className="input-ios text-sm"
                          value={defaults.category}
                          onChange={e => setDefaults(d => ({ ...d, category: e.target.value }))}
                          disabled={importing}
                        >
                          {CATEGORIES.map(c => (
                            <option key={c} value={c}>{c}</option>
                          ))}
                        </select>
                      </div>
                      <div className="input-group">
                        <label className="input-label text-xs">成色</label>
                        <select
                          className="input-ios text-sm"
                          value={defaults.condition}
                          onChange={e => setDefaults(d => ({ ...d, condition: e.target.value }))}
                          disabled={importing}
                        >
                          {CONDITIONS.map(c => (
                            <option key={c} value={c}>{c}</option>
                          ))}
                        </select>
                      </div>
                      <div className="input-group">
                        <label className="input-label text-xs">品牌（选填）</label>
                        <input
                          className="input-ios text-sm"
                          placeholder="统一品牌"
                          value={defaults.brand}
                          onChange={e => setDefaults(d => ({ ...d, brand: e.target.value }))}
                          disabled={importing}
                        />
                      </div>
                      <div className="input-group">
                        <label className="input-label text-xs">发货方式</label>
                        <select
                          className="input-ios text-sm"
                          value={defaults.delivery_method}
                          onChange={e => setDefaults(d => ({ ...d, delivery_method: e.target.value as 'express' | 'pickup' }))}
                          disabled={importing}
                        >
                          <option value="express">快递发货</option>
                          <option value="pickup">自提</option>
                        </select>
                      </div>
                      <div className="input-group">
                        <label className="input-label text-xs">邮费（元）</label>
                        <input
                          type="number"
                          className="input-ios text-sm"
                          min="0" step="0.01"
                          value={defaults.postage}
                          onChange={e => setDefaults(d => ({ ...d, postage: e.target.value }))}
                          disabled={importing}
                        />
                      </div>
                    </div>
                    {/* 编号插入位置 */}
                    <div className="mt-3 pt-3 border-t border-slate-100 dark:border-slate-700 flex items-center gap-3 flex-wrap">
                      <label className="input-label text-xs whitespace-nowrap">编号插入位置</label>
                      <select
                        className="input-ios text-sm w-32"
                        value={codeInsertMode}
                        onChange={e => setCodeInsertMode(e.target.value as 'none' | 'title' | 'description' | 'both')}
                        disabled={importing}
                      >
                        <option value="none">不插入</option>
                        <option value="title">标题最前面</option>
                        <option value="description">内容最后面</option>
                        <option value="both">前后都插入</option>
                      </select>
                      <span className="text-xs text-slate-400">
                        {codeInsertMode === 'title' && '→ 如 "A014 标题"，编号后自动加空格'}
                        {codeInsertMode === 'description' && '→ 内容末尾空一行再加编号'}
                        {codeInsertMode === 'both' && '→ 标题前加空格 + 内容末尾空一行加编号'}
                      </span>
                    </div>
                  </div>
                </div>

                {/* ===== 素材列表（可逐条编辑） ===== */}
                <div className="border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
                  <div className="bg-slate-50 dark:bg-slate-800 px-4 py-2.5 flex items-center justify-between border-b border-slate-200 dark:border-slate-700">
                    <label className="flex items-center gap-2 cursor-pointer select-none">
                        <input
                          ref={el => {
                            if (el) {
                              el.indeterminate = !allSelected && !noneSelected
                            }
                          }}
                          type="checkbox"
                          checked={allSelected}
                          onChange={toggleSelectAll}
                          className="w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500 cursor-pointer"
                        />
                        <span className="text-sm text-slate-600 dark:text-slate-300 font-medium">
                          素材列表 ({materials.length} 条)
                        </span>
                        <span className="text-xs text-slate-400">
                          {allSelected ? '· 全选' : noneSelected ? '· 未选' : `· 已选 ${selectedCount} 条`}
                        </span>
                      </label>
                    <span className="text-xs text-slate-400">
                      {Object.keys(overrides).length > 0 && (
                        <span className="text-amber-500 ml-1">
                          · {Object.keys(overrides).length} 条已单独设置
                        </span>
                      )}
                    </span>
                  </div>
                  <div className="max-h-[50vh] overflow-y-auto divide-y divide-slate-100 dark:divide-slate-700">
                    {materials.map(m => {
                      const isExpanded = expandedCode === m.code
                      const isSelected = selectedCodes.has(m.code)
                      const hasOverride = !!overrides[m.code]
                      const s = getSettings(m.code)

                      return (
                        <div key={m.code}>
                          {/* 素材行 */}
                          <div
                            className={`flex items-start gap-3 px-4 py-3 transition-colors hover:bg-slate-50 dark:hover:bg-slate-700/50 ${
                              isSelected ? 'bg-blue-50/50 dark:bg-blue-900/10' : ''
                            }`}
                          >
                            <label className="mt-0.5 flex-shrink-0 cursor-pointer">
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => toggleSelect(m.code)}
                                className="w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500 cursor-pointer"
                              />
                            </label>
                            {/* 缩略图 */}
                            <div className="w-14 h-14 flex-shrink-0 rounded-lg overflow-hidden bg-slate-100 dark:bg-slate-700 border border-slate-200 dark:border-slate-600">
                              {m.images.length > 0 ? (
                                <img
                                  src={`file://${m.images[0]}`}
                                  alt=""
                                  className="w-full h-full object-cover"
                                  onError={e => {
                                    ;(e.target as HTMLImageElement).style.display = 'none'
                                  }}
                                />
                              ) : (
                                <div className="w-full h-full flex items-center justify-center">
                                  <Image className="w-5 h-5 text-slate-300" />
                                </div>
                              )}
                            </div>
                            {/* 信息区 */}
                            <div
                              className="flex-1 min-w-0 cursor-pointer"
                              onClick={() => toggleSelect(m.code)}
                            >
                              <p className="text-sm font-medium text-slate-800 dark:text-slate-100 truncate">
                                {m.title}
                              </p>
                              <p className="text-xs text-slate-400 mt-0.5 line-clamp-1">
                                {m.description}
                              </p>
                              <div className="flex items-center gap-2 mt-1 flex-wrap">
                                <span className="text-xs text-blue-500 font-mono">{m.code}</span>
                                <span className="text-xs text-slate-400">
                                  <Image className="w-3 h-3 inline mr-0.5" />
                                  {m.image_count} 张图
                                </span>
                                <span className="text-xs text-amber-600 font-medium">¥{s.price}</span>
                                <span className="text-xs text-slate-400">{s.category}</span>
                                {hasOverride && (
                                  <span className="text-xs text-amber-500 bg-amber-50 dark:bg-amber-900/20 px-1 rounded">
                                    <Pencil className="w-2.5 h-2.5 inline mr-0.5" />
                                    已单设
                                  </span>
                                )}
                              </div>
                            </div>
                            {/* 展开按钮 */}
                            <button
                              className="flex-shrink-0 p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
                              onClick={e => {
                                e.stopPropagation()
                                setExpandedCode(isExpanded ? null : m.code)
                              }}
                              title={isExpanded ? '收起' : '单独设置'}
                            >
                              {isExpanded ? (
                                <ChevronUp className="w-4 h-4 text-slate-400" />
                              ) : (
                                <ChevronDown className="w-4 h-4 text-slate-400" />
                              )}
                            </button>
                          </div>

                          {/* 展开的单条编辑区 */}
                          {isExpanded && (
                            <div className="px-4 py-3 bg-slate-50/80 dark:bg-slate-800/50 border-t border-slate-100 dark:border-slate-700">
                              <div className="flex items-center justify-between mb-2">
                                <p className="text-xs font-medium text-slate-500">
                                  单独设置 — <span className="font-mono text-blue-500">{m.code}</span>
                                </p>
                                <button
                                  className="text-xs text-slate-400 hover:text-red-500 transition-colors"
                                  onClick={() => {
                                    setOverrides(prev =>
                                      Object.fromEntries(
                                        Object.entries(prev).filter(([k]) => k !== m.code)
                                      )
                                    )
                                  }}
                                >
                                  恢复默认
                                </button>
                              </div>
                              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                                <div className="input-group">
                                  <label className="input-label text-xs">售价（元）</label>
                                  <input
                                    type="number"
                                    className="input-ios text-sm"
                                    min="0" step="0.01"
                                    placeholder={defaults.price}
                                    value={s.price !== defaults.price ? s.price : ''}
                                    onChange={e => updateOverride(m.code, 'price', e.target.value)}
                                    disabled={importing}
                                  />
                                </div>
                                <div className="input-group">
                                  <label className="input-label text-xs">分类</label>
                                  <select
                                    className="input-ios text-sm"
                                    value={s.category}
                                    onChange={e => updateOverride(m.code, 'category', e.target.value)}
                                    disabled={importing}
                                  >
                                    {CATEGORIES.map(c => (
                                      <option key={c} value={c}>{c}</option>
                                    ))}
                                  </select>
                                </div>
                                <div className="input-group">
                                  <label className="input-label text-xs">成色</label>
                                  <select
                                    className="input-ios text-sm"
                                    value={s.condition}
                                    onChange={e => updateOverride(m.code, 'condition', e.target.value)}
                                    disabled={importing}
                                  >
                                    {CONDITIONS.map(c => (
                                      <option key={c} value={c}>{c}</option>
                                    ))}
                                  </select>
                                </div>
                                <div className="input-group">
                                  <label className="input-label text-xs">品牌</label>
                                  <input
                                    className="input-ios text-sm"
                                    placeholder={defaults.brand || '选填'}
                                    value={s.brand !== defaults.brand ? s.brand : ''}
                                    onChange={e => updateOverride(m.code, 'brand', e.target.value)}
                                    disabled={importing}
                                  />
                                </div>
                                <div className="input-group">
                                  <label className="input-label text-xs">发货方式</label>
                                  <select
                                    className="input-ios text-sm"
                                    value={s.delivery_method}
                                    onChange={e => updateOverride(m.code, 'delivery_method', e.target.value as 'express' | 'pickup')}
                                    disabled={importing}
                                  >
                                    <option value="express">快递发货</option>
                                    <option value="pickup">自提</option>
                                  </select>
                                </div>
                                <div className="input-group">
                                  <label className="input-label text-xs">邮费（元）</label>
                                  <input
                                    type="number"
                                    className="input-ios text-sm"
                                    min="0" step="0.01"
                                    placeholder={defaults.postage}
                                    value={s.postage !== defaults.postage ? s.postage : ''}
                                    onChange={e => updateOverride(m.code, 'postage', e.target.value)}
                                    disabled={importing}
                                  />
                                </div>
                              </div>
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>

                {/* 导入结果 */}
                {importResult && (
                  <div
                    className={`rounded-lg p-4 ${
                      importResult.failed === 0
                        ? 'bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800'
                        : 'bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800'
                    }`}
                  >
                    <p className="text-sm font-medium">
                      导入完成：成功 {importResult.imported} 条
                      {importResult.failed > 0 && (
                        <span className="text-red-500">，失败 {importResult.failed} 条</span>
                      )}
                    </p>
                    {importResult.failed_items.length > 0 && (
                      <ul className="mt-2 text-xs text-red-500 space-y-0.5">
                        {importResult.failed_items.map(f => (
                          <li key={f.code}>
                            <span className="font-mono">{f.code}</span>：{f.reason}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        {/* 底部按钮 */}
        <div className="modal-footer flex-shrink-0">
          <button
            className="btn-ios-secondary"
            onClick={onClose}
            disabled={importing}
          >
            取消
          </button>
          <button
            className="btn-ios-primary"
            onClick={handleImport}
            disabled={!scanned || selectedCount === 0 || importing}
          >
            {importing && <Loader2 className="w-4 h-4 animate-spin" />}
            {importing ? '导入中...' : `导入选中 (${selectedCount})`}
          </button>
        </div>
      </div>
    </div>
  )
}

export default BatchImportModal
