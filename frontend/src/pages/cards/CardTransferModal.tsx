/**
 * 卡券导入导出弹窗
 *
 * 功能：
 * 1. 导出：选择「只导出卡券」或「导出卡券和关联信息」，
 *    后者可按闲鱼账号过滤只导出所选账号的关联信息
 * 2. 导入：选择「只导入卡券」或「导入卡券和关联信息」，
 *    后者可按闲鱼账号过滤只导入所选账号的关联信息
 *
 * 说明：关联信息（卡券↔商品关联）通过商品目录与闲鱼账号关联，
 * 账号过滤 = 只处理所选账号商品的关联记录；「全部账号」= 不过滤。
 */
import { useEffect, useMemo, useState } from 'react'
import { Check, FileSpreadsheet, Loader2, Upload, X } from 'lucide-react'

import {
  exportCards,
  importCards,
  previewCardsImport,
  type CardDuplicateMode,
  type CardImportPreview,
} from '@/api/cards'
import { getAccountDetails } from '@/api/accounts'
import { useUIStore } from '@/store/uiStore'

/** 导入/导出模式 */
type TransferMode = 'cards_only' | 'cards_with_relations'

/** 通用弹窗壳（操作按钮放 footer，固定在弹窗底部不随内容滚动） */
function ModalShell({
  title,
  onClose,
  children,
  footer,
}: {
  title: string
  onClose: () => void
  children: React.ReactNode
  footer?: React.ReactNode
}) {
  return (
    <div className="modal-overlay">
      <div className="modal-content max-w-2xl">
        <div className="modal-header">
          <h2 className="modal-title">{title}</h2>
          <button onClick={onClose} className="modal-close">&times;</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  )
}

/** 模式选择（只导出/导入卡券 vs 含关联信息） */
function ModeSelector({
  mode,
  onChange,
}: {
  mode: TransferMode
  onChange: (m: TransferMode) => void
}) {
  return (
    <div className="space-y-2 mb-4">
      <label
        className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
          mode === 'cards_only'
            ? 'border-blue-300 bg-blue-50/60 dark:border-blue-700 dark:bg-blue-900/20'
            : 'border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50'
        }`}
      >
        <input
          type="radio"
          name="transfer-mode"
          checked={mode === 'cards_only'}
          onChange={() => onChange('cards_only')}
          className="mt-0.5 w-4 h-4 accent-blue-500"
        />
        <div>
          <p className="font-medium text-sm text-slate-900 dark:text-slate-100">只处理卡券</p>
          <p className="text-xs text-slate-500 mt-0.5">仅包含卡券本身的配置与内容</p>
        </div>
      </label>
      <label
        className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
          mode === 'cards_with_relations'
            ? 'border-blue-300 bg-blue-50/60 dark:border-blue-700 dark:bg-blue-900/20'
            : 'border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50'
        }`}
      >
        <input
          type="radio"
          name="transfer-mode"
          checked={mode === 'cards_with_relations'}
          onChange={() => onChange('cards_with_relations')}
          className="mt-0.5 w-4 h-4 accent-blue-500"
        />
        <div>
          <p className="font-medium text-sm text-slate-900 dark:text-slate-100">卡券 + 关联信息</p>
          <p className="text-xs text-slate-500 mt-0.5">
            同时处理卡券与商品的关联信息，可按闲鱼账号过滤
          </p>
        </div>
      </label>
    </div>
  )
}

/** 账号过滤选择器（关联信息按账号过滤） */
function AccountSelector({
  allAccounts,
  onToggleAll,
  selectedIds,
  onToggle,
  accounts,
  loading,
}: {
  allAccounts: boolean
  onToggleAll: () => void
  selectedIds: Set<string>
  onToggle: (id: string) => void
  accounts: { id: string; label: string }[]
  loading: boolean
}) {
  return (
    <div className="mb-4">
      <p className="text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
        关联信息按账号过滤
      </p>
      {loading ? (
        <div className="flex justify-center py-4">
          <Loader2 className="w-5 h-5 animate-spin text-slate-400" />
        </div>
      ) : accounts.length === 0 ? (
        <p className="text-sm text-slate-400 py-2">暂无闲鱼账号</p>
      ) : (
        <div className="space-y-1.5 max-h-[220px] overflow-y-auto">
          <label className="flex items-center gap-3 p-2.5 rounded-lg bg-blue-50 dark:bg-blue-900/20 cursor-pointer">
            <input
              type="checkbox"
              checked={allAccounts}
              onChange={onToggleAll}
              className="w-4 h-4 rounded accent-blue-500"
            />
            <span className="text-sm font-semibold text-blue-700 dark:text-blue-400">全部账号</span>
            <span className="text-xs text-blue-500/70">不过滤，处理所有账号的关联信息</span>
          </label>
          {!allAccounts &&
            accounts.map((acc) => (
              <label
                key={acc.id}
                className={`flex items-center gap-3 p-2.5 rounded-lg border cursor-pointer transition-colors ${
                  selectedIds.has(acc.id)
                    ? 'border-blue-200 bg-blue-50/50 dark:border-blue-800 dark:bg-blue-900/10'
                    : 'border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50'
                }`}
              >
                <input
                  type="checkbox"
                  checked={selectedIds.has(acc.id)}
                  onChange={() => onToggle(acc.id)}
                  className="w-4 h-4 rounded accent-blue-500"
                />
                <span className="text-sm text-slate-700 dark:text-slate-300 truncate">{acc.label}</span>
              </label>
            ))}
        </div>
      )}
    </div>
  )
}

/** 加载账号列表的 Hook（导出/导入弹窗共用） */
function useAccountOptions(open: boolean) {
  const [accounts, setAccounts] = useState<{ id: string; label: string }[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    getAccountDetails()
      .then((list) => {
        if (cancelled) return
        setAccounts(
          (list || []).map((a) => ({
            id: String(a.id),
            label: a.note ? `${a.id}（${a.note}）` : a.id,
          })),
        )
      })
      .catch(() => {
        if (!cancelled) setAccounts([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open])

  return { accounts, loading }
}

// ==================== 导出弹窗 ====================

export function CardExportModal({ onClose }: { onClose: () => void }) {
  const { addToast } = useUIStore()
  const [mode, setMode] = useState<TransferMode>('cards_only')
  const [allAccounts, setAllAccounts] = useState(true)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [exporting, setExporting] = useState(false)
  const { accounts, loading } = useAccountOptions(true)

  const toggleAccount = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleExport = async () => {
    if (mode === 'cards_with_relations' && !allAccounts && selectedIds.size === 0) {
      addToast({ type: 'warning', message: '请至少选择一个闲鱼账号，或选择全部账号' })
      return
    }
    setExporting(true)
    try {
      const includeRelations = mode === 'cards_with_relations'
      const ids = allAccounts ? [] : Array.from(selectedIds)
      const res = await exportCards(includeRelations, ids)
      if (!res.success || !res.blob) {
        addToast({ type: 'error', message: res.message || '导出失败' })
        return
      }
      // 触发浏览器下载
      const url = window.URL.createObjectURL(res.blob)
      const a = document.createElement('a')
      a.href = url
      a.download = res.filename || 'cards_export.xlsx'
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(url)
      addToast({ type: 'success', message: '卡券导出已开始下载' })
      onClose()
    } catch {
      addToast({ type: 'error', message: '导出失败' })
    } finally {
      setExporting(false)
    }
  }

  return (
    <ModalShell
      title="导出卡券"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="btn-ios-secondary">
            取消
          </button>
          <button onClick={handleExport} disabled={exporting} className="btn-ios-primary">
            {exporting ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileSpreadsheet className="w-4 h-4" />}
            导出 Excel
          </button>
        </>
      }
    >
      <ModeSelector mode={mode} onChange={setMode} />
      {mode === 'cards_with_relations' && (
        <AccountSelector
          allAccounts={allAccounts}
          onToggleAll={() => setAllAccounts((v) => !v)}
          selectedIds={selectedIds}
          onToggle={toggleAccount}
          accounts={accounts}
          loading={loading}
        />
      )}
    </ModalShell>
  )
}

// ==================== 导入弹窗 ====================

/** 卡券类型中文标签（预览表格用） */
const CARD_TYPE_LABELS: Record<string, string> = {
  api: 'API',
  text: '文本',
  data: '批量',
  image: '图片',
}

export function CardImportModal({
  onClose,
  onImported,
}: {
  onClose: () => void
  onImported: () => void
}) {
  const { addToast } = useUIStore()
  const [mode, setMode] = useState<TransferMode>('cards_only')
  const [allAccounts, setAllAccounts] = useState(true)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [file, setFile] = useState<File | null>(null)
  const { accounts, loading } = useAccountOptions(true)

  // 预览状态
  const [preview, setPreview] = useState<CardImportPreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const [selectedCardIndexes, setSelectedCardIndexes] = useState<Set<number>>(new Set())
  const [selectedRelationIndexes, setSelectedRelationIndexes] = useState<Set<number>>(new Set())
  const [importing, setImporting] = useState(false)
  // 重复卡券处理方式
  const [duplicateMode, setDuplicateMode] = useState<CardDuplicateMode>('overwrite')

  const toggleAccount = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // 账号过滤参数（含关联模式时生效）
  const accountFilter = useMemo(() => {
    if (mode !== 'cards_with_relations' || allAccounts) return []
    return Array.from(selectedIds)
  }, [mode, allAccounts, selectedIds])

  // 文件/模式/账号过滤变化时自动重新预览
  useEffect(() => {
    if (!file) {
      setPreview(null)
      setPreviewError('')
      return
    }
    let cancelled = false
    setPreviewLoading(true)
    setPreviewError('')
    setPreview(null)
    previewCardsImport(file, mode === 'cards_with_relations', accountFilter)
      .then((res) => {
        if (cancelled) return
        if (res.success && res.data) {
          setPreview(res.data)
          // 默认全选（关联只勾选账号过滤范围内的行）
          setSelectedCardIndexes(new Set(res.data.cards.map((c) => c.index)))
          setSelectedRelationIndexes(
            new Set(res.data.relations.filter((r) => r.in_scope).map((r) => r.index)),
          )
        } else {
          setPreviewError(res.message || '预览解析失败')
        }
      })
      .catch(() => {
        if (!cancelled) setPreviewError('预览解析失败')
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [file, mode, allAccounts, selectedIds, accountFilter])

  const handleFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    if (!f.name.toLowerCase().endsWith('.xlsx')) {
      addToast({ type: 'warning', message: '请选择 .xlsx 格式的 Excel 文件' })
      return
    }
    setFile(f)
  }

  // ---- 选择操作 ----
  const allCardsSelected =
    preview !== null && preview.cards.length > 0 && selectedCardIndexes.size === preview.cards.length

  const selectableRelationCount = preview
    ? preview.relations.filter((r) => r.in_scope).length
    : 0
  const allRelationsSelected =
    selectableRelationCount > 0 && selectedRelationIndexes.size === selectableRelationCount

  const toggleCard = (idx: number) => {
    setSelectedCardIndexes((prev) => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  const toggleRelation = (idx: number) => {
    setSelectedRelationIndexes((prev) => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  const toggleAllCards = () => {
    if (!preview) return
    if (allCardsSelected) setSelectedCardIndexes(new Set())
    else setSelectedCardIndexes(new Set(preview.cards.map((c) => c.index)))
  }

  const toggleAllRelations = () => {
    if (!preview) return
    if (allRelationsSelected) setSelectedRelationIndexes(new Set())
    else
      setSelectedRelationIndexes(
        new Set(preview.relations.filter((r) => r.in_scope).map((r) => r.index)),
      )
  }

  // ---- 执行导入 ----
  const handleImport = async () => {
    if (!file) {
      addToast({ type: 'warning', message: '请先选择要导入的 Excel 文件' })
      return
    }
    if (!preview) {
      addToast({ type: 'warning', message: '请等待文件预览解析完成' })
      return
    }
    if (selectedCardIndexes.size === 0 && selectedRelationIndexes.size === 0) {
      addToast({ type: 'warning', message: '请至少勾选一项要导入的内容' })
      return
    }
    setImporting(true)
    try {
      const includeRelations = mode === 'cards_with_relations'
      const res = await importCards(
        file,
        includeRelations,
        accountFilter,
        Array.from(selectedCardIndexes),
        Array.from(selectedRelationIndexes),
        duplicateMode,
      )
      if (!res.success || !res.data) {
        addToast({ type: 'error', message: res.message || '导入失败' })
        return
      }
      const stats = res.data
      const parts = [`新增卡券 ${stats.card_inserted} 张`, `更新卡券 ${stats.card_updated} 张`]
      if (stats.card_skipped_duplicate > 0) {
        parts.push(`跳过重复卡券 ${stats.card_skipped_duplicate} 张`)
      }
      if (includeRelations) {
        parts.push(
          `新增关联 ${stats.relation_inserted} 条`,
          `已存在跳过 ${stats.relation_skipped_exists} 条`,
        )
        if (stats.relation_skipped_account > 0) {
          parts.push(`因账号过滤跳过 ${stats.relation_skipped_account} 条`)
        }
      }
      addToast({ type: 'success', message: `导入完成：${parts.join('，')}` })
      onImported()
      onClose()
    } catch {
      addToast({ type: 'error', message: '导入失败' })
    } finally {
      setImporting(false)
    }
  }

  return (
    <ModalShell
      title="导入卡券"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="btn-ios-secondary">
            <X className="w-4 h-4" />
            取消
          </button>
          <button
            onClick={handleImport}
            disabled={importing || previewLoading}
            className="btn-ios-primary"
          >
            {importing ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Upload className="w-4 h-4" />
            )}
            开始导入
          </button>
        </>
      }
    >
      <ModeSelector mode={mode} onChange={setMode} />
      {mode === 'cards_with_relations' && (
        <AccountSelector
          allAccounts={allAccounts}
          onToggleAll={() => setAllAccounts((v) => !v)}
          selectedIds={selectedIds}
          onToggle={toggleAccount}
          accounts={accounts}
          loading={loading}
        />
      )}

      {/* 文件选择 */}
      <div className="mb-4">
        <p className="text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">选择文件</p>
        <div
          className={`border-2 border-dashed rounded-xl p-6 text-center transition-colors ${
            file
              ? 'border-blue-400 bg-blue-50 dark:bg-blue-900/10'
              : 'border-slate-300 dark:border-slate-600'
          }`}
        >
          <input
            type="file"
            accept=".xlsx"
            onChange={handleFileSelected}
            className="hidden"
            id="card-import-file"
          />
          <label htmlFor="card-import-file" className="cursor-pointer">
            {file ? (
              <div className="space-y-1">
                <Check className="w-8 h-8 text-blue-500 mx-auto" />
                <p className="text-sm font-medium text-slate-900 dark:text-slate-100">{file.name}</p>
                <p className="text-xs text-slate-500">点击重新选择文件</p>
              </div>
            ) : (
              <div className="space-y-1">
                <Upload className="w-8 h-8 text-slate-400 mx-auto" />
                <p className="text-sm text-slate-600 dark:text-slate-300">点击选择 .xlsx 文件</p>
                <p className="text-xs text-slate-400">支持卡券导出、账号导出生成的 Excel</p>
              </div>
            )}
          </label>
        </div>
      </div>

      {/* 预览区 */}
      {previewLoading && (
        <div className="flex items-center justify-center gap-2 py-6 text-sm text-slate-500">
          <Loader2 className="w-4 h-4 animate-spin" />
          正在解析文件预览...
        </div>
      )}
      {previewError && (
        <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
          {previewError}
        </div>
      )}
      {preview && !previewLoading && (
        <div className="space-y-4 mb-4">
          {/* 重复卡券处理方式（存在重复卡券时显示） */}
          {preview.cards.some((c) => c.exists) && (
            <div className="p-3 rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50/60 dark:bg-amber-900/10">
              <p className="text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
                检测到 {preview.cards.filter((c) => c.exists).length} 张重复卡券（同名同规格已存在），导入时：
              </p>
              <div className="flex flex-wrap gap-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="duplicate-mode"
                    checked={duplicateMode === 'overwrite'}
                    onChange={() => setDuplicateMode('overwrite')}
                    className="w-4 h-4 accent-blue-500"
                  />
                  <span className="text-sm text-slate-700 dark:text-slate-300">
                    覆盖更新（用文件内容覆盖已有卡券）
                  </span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="duplicate-mode"
                    checked={duplicateMode === 'skip'}
                    onChange={() => setDuplicateMode('skip')}
                    className="w-4 h-4 accent-blue-500"
                  />
                  <span className="text-sm text-slate-700 dark:text-slate-300">
                    跳过重复（保留已有卡券不变）
                  </span>
                </label>
              </div>
            </div>
          )}

          {/* 卡券列表 */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-sm font-medium text-slate-700 dark:text-slate-300">
                卡券（已选 {selectedCardIndexes.size} / {preview.cards.length}）
              </p>
              {preview.cards.length > 0 && (
                <button
                  onClick={toggleAllCards}
                  className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                >
                  {allCardsSelected ? '取消全选' : '全选'}
                </button>
              )}
            </div>
            {preview.cards.length === 0 ? (
              <p className="text-sm text-slate-400 py-2">文件中没有卡券数据</p>
            ) : (
              <div className="border border-slate-200 dark:border-slate-700 rounded-lg max-h-[180px] overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-slate-50 dark:bg-slate-800 text-xs text-slate-500">
                    <tr>
                      <th className="p-2 w-10"></th>
                      <th className="p-2 text-left">名称</th>
                      <th className="p-2 text-left">类型</th>
                      <th className="p-2 text-left">规格</th>
                      <th className="p-2 text-left">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.cards.map((c) => (
                      <tr
                        key={c.index}
                        className={`border-t border-slate-100 dark:border-slate-700 cursor-pointer ${
                          selectedCardIndexes.has(c.index)
                            ? 'bg-blue-50/60 dark:bg-blue-900/10'
                            : ''
                        }`}
                        onClick={() => toggleCard(c.index)}
                      >
                        <td className="p-2">
                          <input
                            type="checkbox"
                            checked={selectedCardIndexes.has(c.index)}
                            onChange={() => toggleCard(c.index)}
                            className="w-4 h-4 rounded accent-blue-500"
                          />
                        </td>
                        <td className="p-2 max-w-[180px] truncate" title={c.name}>
                          {c.name}
                        </td>
                        <td className="p-2">
                          <span className="text-xs text-slate-500">
                            {CARD_TYPE_LABELS[c.type] || c.type}
                          </span>
                        </td>
                        <td className="p-2 text-xs text-slate-500">
                          {c.spec_name && c.spec_value
                            ? `${c.spec_name} = ${c.spec_value}`
                            : '-'}
                        </td>
                        <td className="p-2">
                          {c.exists ? (
                            <span
                              className={`text-xs px-1.5 py-0.5 rounded ${
                                duplicateMode === 'skip'
                                  ? 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400'
                                  : 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400'
                              }`}
                            >
                              {duplicateMode === 'skip' ? '跳过' : '更新'}
                            </span>
                          ) : (
                            <span className="text-xs px-1.5 py-0.5 rounded bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">
                              新增
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* 关联列表（含关联模式时） */}
          {mode === 'cards_with_relations' && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <p className="text-sm font-medium text-slate-700 dark:text-slate-300">
                  关联信息（已选 {selectedRelationIndexes.size} / {selectableRelationCount}）
                </p>
                {selectableRelationCount > 0 && (
                  <button
                    onClick={toggleAllRelations}
                    className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                  >
                    {allRelationsSelected ? '取消全选' : '全选'}
                  </button>
                )}
              </div>
              {preview.relations.length === 0 ? (
                <p className="text-sm text-slate-400 py-2">文件中没有关联信息</p>
              ) : (
                <div className="border border-slate-200 dark:border-slate-700 rounded-lg max-h-[180px] overflow-y-auto">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-slate-50 dark:bg-slate-800 text-xs text-slate-500">
                      <tr>
                        <th className="p-2 w-10"></th>
                        <th className="p-2 text-left">卡券名称</th>
                        <th className="p-2 text-left">商品ID</th>
                        <th className="p-2 text-left">归属账号</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.relations.map((r) => {
                        const selectable = r.in_scope
                        return (
                          <tr
                            key={r.index}
                            className={`border-t border-slate-100 dark:border-slate-700 ${
                              selectable ? 'cursor-pointer' : 'opacity-50'
                            } ${
                              selectedRelationIndexes.has(r.index)
                                ? 'bg-blue-50/60 dark:bg-blue-900/10'
                                : ''
                            }`}
                            onClick={() => selectable && toggleRelation(r.index)}
                          >
                            <td className="p-2">
                              <input
                                type="checkbox"
                                checked={selectedRelationIndexes.has(r.index)}
                                disabled={!selectable}
                                onChange={() => toggleRelation(r.index)}
                                className="w-4 h-4 rounded accent-blue-500"
                              />
                            </td>
                            <td className="p-2 max-w-[160px] truncate" title={r.card_name}>
                              {r.card_name}
                            </td>
                            <td className="p-2 font-mono text-xs text-slate-500">{r.item_id}</td>
                            <td className="p-2">
                              {r.account_ids.length > 0 ? (
                                <span className="text-xs text-slate-600 dark:text-slate-300">
                                  {r.account_ids.join(', ')}
                                </span>
                              ) : (
                                <span className="text-xs text-slate-400">未归属（商品不在当前库）</span>
                              )}
                              {!selectable && (
                                <span className="text-xs text-red-500 ml-1">不在所选账号范围</span>
                              )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}

    </ModalShell>
  )
}
