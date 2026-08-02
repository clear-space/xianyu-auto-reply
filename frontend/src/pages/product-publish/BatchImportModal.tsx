/**
 * 批量导入素材弹窗
 *
 * 功能：
 * 1. 客户端选择本地素材目录（支持远程访问场景）
 * 2. 浏览器端解析目录结构（txt + 图片）
 * 3. 预览扫描结果，统一设置字段，也可逐条单独调整
 * 4. 确认后上传图片 + 元数据到服务器，批量导入到素材库
 *
 * 目录结构要求：每个子文件夹为一个素材，包含：
 * - 一个 .txt 文件（第一行=标题，最后非空行=编号，中间=描述）
 * - 若干 .jpg/.png 图片（按文件名中数字排序）
 */
import { useState, useEffect, useRef } from 'react'
import { X, Loader2, FolderOpen, Image, ChevronDown, ChevronUp, Pencil, FolderUp } from 'lucide-react'
import { useUIStore } from '@/store/uiStore'
import { batchImportMaterialsUpload } from '@/api/productPublish'

const CATEGORIES = ['数码家电', '服饰鞋包', '家居日用', '图书音像', '美妆个护', '母婴用品', '运动户外', '食品生鲜', '虚拟商品', '其它闲置', '其他']
const CONDITIONS = ['全新', '99新', '95新', '9成新', '8成新', '7成新以下']
const IMAGE_EXTS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'])
const TXT_EXT = '.txt'

/** 单条素材的本地数据 */
interface LocalMaterial {
  code: string
  folder_name: string
  title: string
  description: string
  image_count: number
  category: string
  price: number
}

/** 单条素材的可编辑字段 */
interface ItemSettings {
  price: string
  original_price: string
  category: string
  condition: string
  brand: string
  delivery_method: 'express' | 'pickup'
  postage: string
  stock: string
}

interface Props {
  onClose: () => void
  onImported: () => void
}

// ==================== 客户端 TXT 解析（移植自后端 Python 逻辑） ====================

/** 推断分类（与后端 product_publish.py scan_directory 保持一致） */
function inferCategory(txtContent: string): string {
  if (txtContent.includes('虚拟商品')) return '虚拟商品'
  if (/数码|手机|电脑|电子/.test(txtContent)) return '数码家电'
  if (/服饰|鞋|包|衣服|穿/.test(txtContent)) return '服饰鞋包'
  if (/家居|日用|家具|收纳/.test(txtContent)) return '家居日用'
  if (/书|音像|DVD|CD/.test(txtContent)) return '图书音像'
  if (/美妆|护肤|化妆|个护/.test(txtContent)) return '美妆个护'
  if (/母婴|宝宝|孕/.test(txtContent)) return '母婴用品'
  if (/运动|户外|健身|瑜伽/.test(txtContent)) return '运动户外'
  if (/食品|生鲜|零食|饮料/.test(txtContent)) return '食品生鲜'
  if (/PPT|模板|简历|教程|素材|资料|网盘|电子/.test(txtContent)) return '虚拟商品'
  return '虚拟商品'
}

/** 解析 txt 文本，提取标题/编号/描述 */
function parseTxtContent(text: string): { title: string; code: string; description: string } | null {
  const lines = text.split('\n').map(l => l.trim())
  const nonEmpty = lines.filter(Boolean)
  if (nonEmpty.length < 2) return null

  // 第一非空行 = 标题，去掉【xxx】前缀
  const rawTitle = nonEmpty[0]
  const title = rawTitle.replace(/^【[^】]*】\s*/, '').trim()

  // 最后非空行 = 编号
  const code = nonEmpty[nonEmpty.length - 1].trim()

  // 中间行 = 描述
  const description = nonEmpty.length <= 2
    ? title
    : nonEmpty.slice(1, -1).join('\n').trim() || title

  return { title, code, description }
}

/** 按文件名中的数字排序（1.jpg, 2.jpg, ...） */
function sortByNumericFilename(files: File[]): File[] {
  return [...files].sort((a, b) => {
    const numA = parseInt((a.name.match(/(\d+)/) ?? ['0'])[0], 10)
    const numB = parseInt((b.name.match(/(\d+)/) ?? ['0'])[0], 10)
    return numA - numB
  })
}

/** 读取 Blob 为指定编码的文本 */
function readBlobAsText(blob: Blob, encoding: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    if (encoding === 'gbk' || encoding === 'gb2312') {
      // TextDecoder 方式
      const fr = new FileReader()
      fr.onload = () => {
        try {
          const decoder = new TextDecoder(encoding)
          resolve(decoder.decode(fr.result as ArrayBuffer))
        } catch {
          reject(new Error('GBK decode failed'))
        }
      }
      fr.onerror = () => reject(fr.error)
      fr.readAsArrayBuffer(blob)
    } else {
      reader.readAsText(blob, encoding)
    }
  })
}

/** 尝试读取 txt 文件内容（UTF-8 → GBK fallback） */
async function readTxtFile(file: File): Promise<string> {
  // 先尝试 UTF-8
  const utf8 = await readBlobAsText(file, 'utf-8')
  // 检查是否有乱码特征（大量替换字符）
  const replacementCount = (utf8.match(/�/g) || []).length
  if (replacementCount > 0 && utf8.length > 10) {
    // 尝试 GBK
    try {
      return await readBlobAsText(file, 'gbk')
    } catch {
      // GBK 失败，返回 UTF-8 结果
    }
  }
  return utf8
}

// ==================== 组件 ====================

export function BatchImportModal({ onClose, onImported }: Props) {
  const { addToast } = useUIStore()

  // 扫描状态
  const [scanning, setScanning] = useState(false)
  const [materials, setMaterials] = useState<LocalMaterial[]>([])
  const [scanned, setScanned] = useState(false)

  // 存储原始 File 对象（按 code 索引）：code → 图片 File[]
  const materialFilesRef = useRef<Map<string, File[]>>(new Map())
  // blob URL 列表（用于清理）
  const blobUrlsRef = useRef<string[]>([])
  // 缩略图缓存：code → 第一张图片的 blob URL
  const thumbnailCacheRef = useRef<Map<string, string>>(new Map())

  // 选择状态
  const [selectedCodes, setSelectedCodes] = useState<Set<string>>(new Set())

  // 展开单条编辑的素材编号
  const [expandedCode, setExpandedCode] = useState<string | null>(null)

  // 清理定时器
  const timerRef = useRef<ReturnType<typeof setTimeout>>()
  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current) }, [])

  // 组件卸载时清理所有 blob URL
  useEffect(() => {
    const urls = blobUrlsRef.current
    return () => {
      urls.forEach(u => URL.revokeObjectURL(u))
    }
  }, [])

  /** 清理所有 blob URL */
  const revokeAllBlobs = () => {
    blobUrlsRef.current.forEach(u => URL.revokeObjectURL(u))
    blobUrlsRef.current = []
    thumbnailCacheRef.current.clear()
    materialFilesRef.current.clear()
  }

  // 统一字段默认值
  const [defaults, setDefaults] = useState<ItemSettings>({
    price: '9.9',
    original_price: '',
    category: '虚拟商品',
    condition: '全新',
    brand: '',
    delivery_method: 'express',
    postage: '0',
    stock: '9999',
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

  /** 构建一条素材的最终设置 */
  function buildItemSettings(defaults: ItemSettings, overrides: Partial<ItemSettings>): ItemSettings {
    return {
      price: overrides.price ?? defaults.price,
      original_price: overrides.original_price ?? defaults.original_price,
      category: overrides.category ?? defaults.category,
      condition: overrides.condition ?? defaults.condition,
      brand: overrides.brand ?? defaults.brand,
      delivery_method: overrides.delivery_method ?? defaults.delivery_method,
      postage: overrides.postage ?? defaults.postage,
      stock: overrides.stock ?? defaults.stock,
    }
  }

  /** 获取某条素材的最终设置 */
  const getSettings = (code: string): ItemSettings =>
    buildItemSettings(defaults, overrides[code] || {})

  /** 更新某条素材的单个字段 */
  const updateOverride = (code: string, field: keyof ItemSettings, value: string) => {
    setOverrides(prev => {
      const current = prev[code] || {}
      const updated = { ...current, [field]: value }
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

  /** 处理文件夹选择（webkitdirectory） */
  const handleFolderSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return

    setScanning(true)
    setScanned(false)
    setMaterials([])
    setSelectedCodes(new Set())
    setOverrides({})
    setExpandedCode(null)
    setImportResult(null)
    revokeAllBlobs()

    try {
      // 按直接子目录分组
      const folderMap = new Map<string, { txtFiles: File[]; imgFiles: File[] }>()

      for (let i = 0; i < files.length; i++) {
        const file = files[i]
        // webkitRelativePath: "素材目录/A001/1.jpg"
        const relativePath = (file as any).webkitRelativePath || file.name
        const parts = relativePath.split('/')
        if (parts.length < 2) continue // 跳过根目录文件

        const folderName = parts[parts.length - 2] // 直接父目录名
        const fileName = parts[parts.length - 1]
        const ext = '.' + fileName.split('.').pop()?.toLowerCase()

        if (!folderMap.has(folderName)) {
          folderMap.set(folderName, { txtFiles: [], imgFiles: [] })
        }
        const entry = folderMap.get(folderName)!

        if (ext === TXT_EXT || fileName.endsWith('.txt')) {
          entry.txtFiles.push(file)
        } else if (IMAGE_EXTS.has(ext)) {
          entry.imgFiles.push(file)
        }
      }

      if (folderMap.size === 0) {
        addToast({ type: 'warning', message: '未找到有效的素材子目录，请确认目录结构' })
        return
      }

      // 解析每个子目录
      const parsed: LocalMaterial[] = []
      const fileMap = new Map<string, File[]>()

      // 按文件夹名排序
      const sortedFolders = [...folderMap.entries()].sort(([a], [b]) => a.localeCompare(b))

      for (const [folderName, { txtFiles, imgFiles }] of sortedFolders) {
        try {
          if (txtFiles.length === 0) {
            console.warn(`跳过无txt文件的目录: ${folderName}`)
            continue
          }

          // 读第一个 txt 文件
          const txtFile = txtFiles.sort((a, b) => a.name.localeCompare(b.name))[0]
          const txtContent = await readTxtFile(txtFile)
          const parsed2 = parseTxtContent(txtContent)
          if (!parsed2) {
            console.warn(`txt文件内容不足: ${folderName}/${txtFile.name}`)
            continue
          }

          // 排序图片
          const sorted = sortByNumericFilename(imgFiles)

          // 推断分类
          const category = inferCategory(txtContent)

          parsed.push({
            code: parsed2.code,
            folder_name: folderName,
            title: parsed2.title,
            description: parsed2.description,
            image_count: sorted.length,
            category,
            price: 0,
          })

          fileMap.set(parsed2.code, sorted)
        } catch (err) {
          console.warn(`解析目录异常 ${folderName}:`, err)
        }
      }

      if (parsed.length === 0) {
        addToast({ type: 'warning', message: '未找到有效的素材，请确认每个子目录包含 .txt 和图片文件' })
        return
      }

      materialFilesRef.current = fileMap

      // 预创建缩略图 blob URL
      fileMap.forEach((imgFiles, code) => {
        if (imgFiles.length > 0) {
          const url = URL.createObjectURL(imgFiles[0])
          blobUrlsRef.current.push(url)
          thumbnailCacheRef.current.set(code, url)
        }
      })

      setMaterials(parsed)
      setSelectedCodes(new Set(parsed.map(m => m.code)))
      setScanned(true)
      addToast({ type: 'success', message: `扫描完成，发现 ${parsed.length} 个素材` })
    } catch (err) {
      addToast({ type: 'error', message: '扫描失败，请重试' })
      console.error('目录扫描异常:', err)
    } finally {
      setScanning(false)
      // 重置 input 以便能重新选择同一目录
      e.target.value = ''
    }
  }

  /** 获取某条素材的缩略图 blob URL（从缓存读取） */
  const getThumbnail = (code: string): string | undefined => {
    return thumbnailCacheRef.current.get(code)
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
      const formData = new FormData()

      // 构建元数据数组（不含图片文件本身）
      const metadataList = selected.map((m) => {
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
          image_count: m.image_count,
          price: parseFloat(s.price) || 0,
          original_price: s.original_price ? parseFloat(s.original_price) : null,
          category: s.category,
          condition: s.condition,
          brand: s.brand.trim(),
          delivery_method: s.delivery_method,
          postage: parseFloat(s.postage) || 0,
          stock: parseInt(s.stock) || 9999,
        }
      })

      formData.append('materials', JSON.stringify(metadataList))

      // 添加图片文件：img_{materialIndex}_{imageIndex}
      selected.forEach((m, i) => {
        const imgFiles = materialFilesRef.current.get(m.code)
        if (imgFiles) {
          imgFiles.forEach((file, j) => {
            formData.append(`img_${i}_${j}`, file, file.name)
          })
        }
      })

      const res = await batchImportMaterialsUpload(formData)
      if (res.success && res.data) {
        setImportResult(res.data)
        if (res.data.failed === 0) {
          addToast({ type: 'success', message: `成功导入 ${res.data.imported} 条素材！` })
          timerRef.current = setTimeout(() => {
            revokeAllBlobs()
            onImported()
          }, 1200)
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

  // 隐藏 input ref
  const fileInputRef = useRef<HTMLInputElement>(null)

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
            {/* 目录选择 */}
            <div className="input-group">
              <label className="input-label">选择本地素材目录</label>

              {/* 拖拽区域 */}
              <div
                className="border-2 border-dashed border-slate-300 dark:border-slate-600 rounded-lg p-6 text-center hover:border-blue-400 dark:hover:border-blue-500 transition-colors cursor-pointer"
                onClick={() => fileInputRef.current?.click()}
                onDragOver={e => { e.preventDefault(); e.stopPropagation() }}
                onDrop={e => {
                  e.preventDefault(); e.stopPropagation()
                  // 提示用户使用按钮选择（拖拽 webkitdirectory 需要特殊处理）
                  addToast({ type: 'warning', message: '请点击按钮选择文件夹（而非拖拽）' })
                }}
              >
                <FolderUp className="w-10 h-10 mx-auto text-slate-300 dark:text-slate-500 mb-2" />
                <p className="text-sm text-slate-500 dark:text-slate-400">
                  点击选择包含素材的本地文件夹
                </p>
                <p className="text-xs text-slate-400 mt-1">
                  每个子文件夹 = 一个素材（含 .txt 元数据 + 图片）
                </p>
              </div>

              <input
                ref={fileInputRef}
                type="file"
                // @ts-ignore — webkitdirectory 非标准属性
                webkitdirectory=""
                directory=""
                multiple
                className="hidden"
                onChange={handleFolderSelect}
                disabled={scanning || importing}
              />

              <p className="text-xs text-slate-400 mt-2 text-center">
                推荐使用 Chrome / Edge 浏览器，Firefox 可能不完全支持文件夹选择
              </p>
            </div>

            {/* 扫描中 */}
            {scanning && (
              <div className="flex items-center justify-center py-12 text-slate-400">
                <Loader2 className="w-8 h-8 animate-spin text-blue-500 mr-3" />
                <span>正在解析目录结构...</span>
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
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
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
                        <label className="input-label text-xs">原价（选填）</label>
                        <input
                          type="number"
                          className="input-ios text-sm"
                          min="0" step="0.01"
                          placeholder="划线价"
                          value={defaults.original_price}
                          onChange={e => setDefaults(d => ({ ...d, original_price: e.target.value }))}
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
                      <div className="input-group">
                        <label className="input-label text-xs">库存</label>
                        <input
                          type="number"
                          className="input-ios text-sm"
                          min="0" step="1"
                          value={defaults.stock}
                          onChange={e => setDefaults(d => ({ ...d, stock: e.target.value }))}
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
                      const thumbnail = getThumbnail(m.code)

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
                              {thumbnail ? (
                                <img
                                  src={thumbnail}
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
                              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
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
                                  <label className="input-label text-xs">原价（选填）</label>
                                  <input
                                    type="number"
                                    className="input-ios text-sm"
                                    min="0" step="0.01"
                                    placeholder={defaults.original_price || '划线价'}
                                    value={s.original_price !== defaults.original_price ? s.original_price : ''}
                                    onChange={e => updateOverride(m.code, 'original_price', e.target.value)}
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
                                <div className="input-group">
                                  <label className="input-label text-xs">库存</label>
                                  <input
                                    type="number"
                                    className="input-ios text-sm"
                                    min="0" step="1"
                                    placeholder={defaults.stock}
                                    value={s.stock !== defaults.stock ? s.stock : ''}
                                    onChange={e => updateOverride(m.code, 'stock', e.target.value)}
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
