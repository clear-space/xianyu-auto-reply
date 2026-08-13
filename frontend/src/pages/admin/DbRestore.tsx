/**
 * 数据库恢复页面
 *
 * 功能：
 * 1. 上传 .sql.gz 备份文件或选择已有备份文件
 * 2. 解析备份文件，按分类展示包含的表
 * 3. 选择要恢复的数据分类，执行恢复
 * 4. 展示恢复结果（成功/失败/跳过）
 *
 * 流程：选择文件 → 解析预览 → 选择分类 → 确认执行 → 查看结果
 */
import { useEffect, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle,
  ChevronDown,
  ChevronRight,
  Database,
  Download,
  FileUp,
  FolderOpen,
  HardDrive,
  Loader2,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldAlert,
  Upload,
  X,
  XCircle,
} from 'lucide-react'

import {
  downloadBackupFile,
  executeRestore,
  getBackupFiles,
  parseExistingBackup,
  previewBackupFile,
  uploadAndParseBackup,
  type BackupFileItem,
  type PreviewResult,
  type RestoreCategoryInfo,
  type RestoreExecuteResult,
  type RestoreMode,
  type RestoreParseResult,
} from '@/api/admin'
import { PageLoading } from '@/components/common/Loading'
import { getApiErrorMessage } from '@/utils/apiError'
import { useAuthStore } from '@/store/authStore'
import { useUIStore } from '@/store/uiStore'

/** Step indicator states */
type Step = 'select_source' | 'preview' | 'configure' | 'executing' | 'result'

/** Tab for file source selection */
type FileSourceTab = 'upload' | 'existing'

/** Format bytes to human-readable */
const fmtSize = (s: number): string => {
  if (s < 1024) return `${s} B`
  if (s < 1024 * 1024) return `${(s / 1024).toFixed(1)} KB`
  if (s < 1024 * 1024 * 1024) return `${(s / 1024 / 1024).toFixed(2)} MB`
  return `${(s / 1024 / 1024 / 1024).toFixed(2)} GB`
}

/** Format milliseconds to human-readable */
const fmtDuration = (ms: number): string => {
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

export function DbRestore() {
  const { addToast } = useUIStore()
  const { isAuthenticated, token, _hasHydrated } = useAuthStore()

  // ---- Wizard state ----
  const [step, setStep] = useState<Step>('select_source')
  const [loading, setLoading] = useState(false)

  // ---- Step 1: file source ----
  const [sourceTab, setSourceTab] = useState<FileSourceTab>('upload')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [existingFiles, setExistingFiles] = useState<BackupFileItem[]>([])
  const [selectedExistingFile, setSelectedExistingFile] = useState('')
  const [filesLoading, setFilesLoading] = useState(false)

  // ---- Parse result ----
  const [parseResult, setParseResult] = useState<RestoreParseResult | null>(null)
  const [parseError, setParseError] = useState('')

  // ---- Step 2: restore mode selection ----
  const [restoreMode, setRestoreMode] = useState<RestoreMode>('all')
  const [selectedAccountIds, setSelectedAccountIds] = useState<Set<string>>(new Set())
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set())
  const [confirmed, setConfirmed] = useState(false)

  // ---- Execution result ----
  const [executeResult, setExecuteResult] = useState<RestoreExecuteResult | null>(null)
  const [executeError, setExecuteError] = useState('')

  // ---- Auth guard ----
  const ready = _hasHydrated && isAuthenticated && token

  // ---- Load existing backup files when tab is selected ----
  useEffect(() => {
    if (!ready || sourceTab !== 'existing') return
    loadExistingFiles()
  }, [ready, sourceTab])

  const loadExistingFiles = async () => {
    setFilesLoading(true)
    try {
      const res = await getBackupFiles()
      if (res.success) {
        setExistingFiles(res.data || [])
      }
    } catch {
      // silent
    } finally {
      setFilesLoading(false)
    }
  }

  // ---- Reset all state ----
  const resetAll = () => {
    setStep('select_source')
    setSelectedFile(null)
    setSelectedExistingFile('')
    setParseResult(null)
    setParseError('')
    setRestoreMode('all')
    setSelectedAccountIds(new Set())
    setExpandedCategories(new Set())
    setConfirmed(false)
    setExecuteResult(null)
    setExecuteError('')
  }

  // ---- Handle file upload ----
  const handleFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (!file.name.endsWith('.gz') && !file.name.endsWith('.sql.gz')) {
      addToast({ type: 'warning', message: '请选择 .sql.gz 格式的备份文件' })
      return
    }
    setSelectedFile(file)
    setParseError('')
  }

  // ---- Parse uploaded file ----
  const handleParseUpload = async () => {
    if (!selectedFile) return
    setLoading(true)
    setParseError('')
    try {
      const res = await uploadAndParseBackup(selectedFile)
      if (res.success && res.data) {
        setParseResult(res.data)
        setStep('preview')
      } else {
        setParseError(res.message || '解析失败')
      }
    } catch (error) {
      setParseError(getApiErrorMessage(error, '解析备份文件失败'))
    } finally {
      setLoading(false)
    }
  }

  // ---- Parse existing file ----
  const handleParseExisting = async () => {
    if (!selectedExistingFile) return
    setLoading(true)
    setParseError('')
    try {
      const res = await parseExistingBackup(selectedExistingFile)
      if (res.success && res.data) {
        setParseResult(res.data)
        setStep('preview')
      } else {
        setParseError(res.message || '解析失败')
      }
    } catch (error) {
      setParseError(getApiErrorMessage(error, '解析备份文件失败'))
    } finally {
      setLoading(false)
    }
  }

  // ---- Download backup file ----
  const [downloadingFile, setDownloadingFile] = useState<string | null>(null)

  // ---- Preview state ----
  const [previewLoadingFile, setPreviewLoadingFile] = useState<string | null>(null)
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null)
  const [showPreviewModal, setShowPreviewModal] = useState(false)

  const handleDownload = async (fileName: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setDownloadingFile(fileName)
    try {
      const res = await downloadBackupFile(fileName)
      if (res.success && res.blob) {
        const url = window.URL.createObjectURL(res.blob)
        const a = document.createElement('a')
        a.href = url
        a.download = res.filename || fileName
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        window.URL.revokeObjectURL(url)
      } else {
        addToast({ type: 'error', message: res.message || '下载失败' })
      }
    } catch {
      addToast({ type: 'error', message: '下载备份文件失败' })
    } finally {
      setDownloadingFile(null)
    }
  }

  // ---- Preview backup ----
  const handlePreview = async (fileName: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setPreviewLoadingFile(fileName)
    setPreviewResult(null)
    try {
      const res = await previewBackupFile(fileName)
      if (res.success && res.data) {
        setPreviewResult(res.data)
        setShowPreviewModal(true)
      } else {
        addToast({ type: 'error', message: res.message || '预览备份文件失败' })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '预览备份文件失败') })
    } finally {
      setPreviewLoadingFile(null)
    }
  }

  // ---- Toggle account selection（按账号恢复模式） ----
  const toggleAccount = (accountId: string) => {
    setSelectedAccountIds((prev) => {
      const next = new Set(prev)
      if (next.has(accountId)) {
        next.delete(accountId)
      } else {
        next.add(accountId)
      }
      return next
    })
  }

  const toggleAllAccounts = () => {
    if (!parseResult) return
    const allIds = parseResult.accounts.map((a) => a.account_id)
    if (selectedAccountIds.size === allIds.length) {
      setSelectedAccountIds(new Set())
    } else {
      setSelectedAccountIds(new Set(allIds))
    }
  }

  const toggleExpandCategory = (key: string) => {
    setExpandedCategories((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  // ---- Execute restore ----
  const handleExecute = async () => {
    if (!parseResult) return
    if (restoreMode === 'selected_accounts' && selectedAccountIds.size === 0) return
    setStep('executing')
    setExecuteError('')
    try {
      const res = await executeRestore(
        parseResult.reference_id,
        [],
        restoreMode,
        restoreMode === 'selected_accounts' ? Array.from(selectedAccountIds) : [],
      )
      if (res.success && res.data) {
        setExecuteResult(res.data)
      } else {
        setExecuteError(res.message || '恢复执行失败')
      }
    } catch (error) {
      setExecuteError(getApiErrorMessage(error, '恢复执行失败'))
    } finally {
      setStep('result')
    }
  }

  // ---- Derived ----
  const allAccountsSelected = parseResult
    ? parseResult.accounts.length > 0 && selectedAccountIds.size === parseResult.accounts.length
    : false

  // 执行按钮可用性
  const canExecute = restoreMode === 'selected_accounts'
    ? confirmed && selectedAccountIds.size > 0
    : confirmed

  // ---- Initial load guard ----
  if (!_hasHydrated || !isAuthenticated || !token) {
    return <PageLoading />
  }

  // ==================== Step indicator ====================
  const steps: { key: Step; label: string }[] = [
    { key: 'select_source', label: '选择备份文件' },
    { key: 'preview', label: '预览解析结果' },
    { key: 'configure', label: '选择恢复范围' },
    { key: 'executing', label: '执行恢复' },
  ]
  const currentStepIdx = steps.findIndex((s) => s.key === (step === 'result' ? 'executing' : step))

  const renderStepIndicator = () => (
    <div className="flex items-center gap-2 mb-6">
      {steps.map((s, idx) => (
        <div key={s.key} className="flex items-center gap-2">
          <div
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
              idx <= currentStepIdx
                ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                : 'bg-slate-100 text-slate-400 dark:bg-slate-700 dark:text-slate-500'
            }`}
          >
            <span className={`w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold ${
              idx < currentStepIdx
                ? 'bg-blue-500 text-white'
                : idx === currentStepIdx
                ? 'bg-blue-500 text-white'
                : 'bg-slate-300 text-slate-500 dark:bg-slate-600'
            }`}>
              {idx < currentStepIdx ? <CheckCircle className="w-3.5 h-3.5" /> : idx + 1}
            </span>
            {s.label}
          </div>
          {idx < steps.length - 1 && (
            <div className={`w-6 h-0.5 ${idx < currentStepIdx ? 'bg-blue-300' : 'bg-slate-200 dark:bg-slate-600'}`} />
          )}
        </div>
      ))}
    </div>
  )

  // ==================== Step: select source ====================
  const renderSelectSource = () => (
    <>
      {/* Tabs */}
      <div className="flex gap-1 mb-4 bg-slate-100 dark:bg-slate-800 rounded-lg p-1 w-fit">
        <button
          onClick={() => setSourceTab('upload')}
          className={`flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
            sourceTab === 'upload'
              ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 shadow-sm'
              : 'text-slate-500 dark:text-slate-400 hover:text-slate-700'
          }`}
        >
          <Upload className="w-4 h-4" />
          上传备份文件
        </button>
        <button
          onClick={() => setSourceTab('existing')}
          className={`flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
            sourceTab === 'existing'
              ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 shadow-sm'
              : 'text-slate-500 dark:text-slate-400 hover:text-slate-700'
          }`}
        >
          <FolderOpen className="w-4 h-4" />
          从现有备份选择
        </button>
      </div>

      {/* Upload tab */}
      {sourceTab === 'upload' && (
        <div className="vben-card">
          <div className="vben-card-body">
            <div
              className={`border-2 border-dashed rounded-xl p-10 text-center transition-colors cursor-pointer ${
                selectedFile
                  ? 'border-blue-400 bg-blue-50 dark:bg-blue-900/10'
                  : 'border-slate-300 dark:border-slate-600 hover:border-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/10'
              }`}
            >
              <input
                type="file"
                accept=".sql.gz,.gz"
                onChange={handleFileSelected}
                className="hidden"
                id="restore-file-input"
              />
              <label htmlFor="restore-file-input" className="cursor-pointer">
                {selectedFile ? (
                  <div className="space-y-2">
                    <FileUp className="w-10 h-10 text-blue-500 mx-auto" />
                    <p className="font-medium text-slate-900 dark:text-slate-100">{selectedFile.name}</p>
                    <p className="text-sm text-slate-500">{fmtSize(selectedFile.size)}</p>
                    <p className="text-xs text-slate-400">点击重新选择文件</p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <Upload className="w-10 h-10 text-slate-400 mx-auto" />
                    <p className="font-medium text-slate-600 dark:text-slate-300">
                      点击选择或拖拽 .sql.gz 文件
                    </p>
                    <p className="text-sm text-slate-400">支持 gzip 压缩的 SQL 备份文件</p>
                  </div>
                )}
              </label>
            </div>

            {selectedFile && (
              <div className="flex justify-center mt-4">
                <button onClick={handleParseUpload} disabled={loading} className="btn-ios-primary">
                  {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Database className="w-4 h-4" />}
                  解析文件
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Existing files tab */}
      {sourceTab === 'existing' && (
        <div className="vben-card">
          <div className="vben-card-header">
            <h2 className="vben-card-title">
              <HardDrive className="w-4 h-4" />
              备份目录文件
            </h2>
            <button onClick={loadExistingFiles} className="btn-ios-secondary btn-sm">
              <RefreshCw className="w-3.5 h-3.5" />
              刷新
            </button>
          </div>
          <div className="vben-card-body">
            {filesLoading ? (
              <div className="flex justify-center py-8">
                <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
              </div>
            ) : existingFiles.length === 0 ? (
              <div className="text-center py-8 text-slate-400">
                <HardDrive className="w-12 h-12 mx-auto mb-2 opacity-50" />
                <p>暂无备份文件</p>
                <p className="text-xs mt-1">scheduler 自动备份的文件会出现在这里</p>
              </div>
            ) : (
              <div className="max-h-[340px] overflow-y-auto">
                <table className="table-ios">
                  <thead className="sticky top-0 bg-white dark:bg-slate-800">
                    <tr>
                      <th>文件名</th>
                      <th>大小</th>
                      <th>修改时间</th>
                      <th>下载</th>
                      <th>预览</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {existingFiles.map((f) => (
                      <tr
                        key={f.name}
                        className={`cursor-pointer hover:bg-blue-50 dark:hover:bg-blue-900/10 ${
                          selectedExistingFile === f.name ? 'bg-blue-50 dark:bg-blue-900/20' : ''
                        }`}
                        onClick={() => setSelectedExistingFile(f.name)}
                      >
                        <td className="font-medium text-blue-600 dark:text-blue-400 max-w-[300px] truncate" title={f.name}>
                          {f.name}
                        </td>
                        <td className="text-slate-500">{f.size_formatted}</td>
                        <td className="text-slate-500 text-sm">
                          {new Date(f.modified_at).toLocaleString('zh-CN')}
                        </td>
                        <td>
                          <button
                            onClick={(e) => handleDownload(f.name, e)}
                            disabled={downloadingFile === f.name}
                            className="p-1.5 text-slate-400 hover:text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-md transition-colors"
                            title={`下载 ${f.name}`}
                          >
                            {downloadingFile === f.name ? (
                              <Loader2 className="w-4 h-4 animate-spin" />
                            ) : (
                              <Download className="w-4 h-4" />
                            )}
                          </button>
                        </td>
                        <td>
                          <button
                            onClick={(e) => handlePreview(f.name, e)}
                            disabled={previewLoadingFile === f.name}
                            className="p-1.5 text-slate-400 hover:text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-md transition-colors"
                            title={`预览 ${f.name}`}
                          >
                            {previewLoadingFile === f.name ? (
                              <Loader2 className="w-4 h-4 animate-spin" />
                            ) : (
                              <Search className="w-4 h-4" />
                            )}
                          </button>
                        </td>
                        <td>
                          {selectedExistingFile === f.name && (
                            <CheckCircle className="w-4 h-4 text-blue-500" />
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {selectedExistingFile && (
              <div className="flex justify-center mt-4">
                <button onClick={handleParseExisting} disabled={loading} className="btn-ios-primary">
                  {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Database className="w-4 h-4" />}
                  解析文件
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Parse error */}
      {parseError && (
        <div className="mt-4 p-4 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 flex items-start gap-3">
          <XCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
          <div>
            <p className="font-medium text-red-700 dark:text-red-400">解析失败</p>
            <p className="text-sm text-red-600 dark:text-red-400 mt-1">{parseError}</p>
          </div>
        </div>
      )}
    </>
  )

  // ==================== Step: preview ====================
  const renderPreview = () => {
    if (!parseResult) return null
    return (
      <>
        {/* Summary card */}
        <div className="vben-card mb-4">
          <div className="vben-card-body">
            <div className="flex items-center gap-3 mb-3">
              <Database className="w-8 h-8 text-blue-500" />
              <div>
                <p className="font-semibold text-slate-900 dark:text-slate-100">{parseResult.source_file}</p>
                <p className="text-sm text-slate-500">
                  共 <span className="font-medium text-slate-700 dark:text-slate-300">{parseResult.total_tables}</span> 张表，
                  <span className="font-medium text-slate-700 dark:text-slate-300 ml-1">{parseResult.categories.length}</span> 个数据分类
                </p>
              </div>
            </div>

            {/* Category cards grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {parseResult.categories.map((cat) => (
                <CategoryCard
                  key={cat.key}
                  category={cat}
                  expanded={expandedCategories.has(cat.key)}
                  onToggleExpand={() => toggleExpandCategory(cat.key)}
                />
              ))}
            </div>
          </div>
        </div>

        {/* Actions */}
        <div className="flex gap-3">
          <button onClick={resetAll} className="btn-ios-secondary">
            重新选择文件
          </button>
          <button onClick={() => setStep('configure')} className="btn-ios-primary">
            下一步：选择恢复范围
          </button>
        </div>
      </>
    )
  }

  // ==================== Step: configure ====================
  const renderConfigure = () => {
    if (!parseResult) return null
    return (
      <>
        {/* Restore mode selection */}
        <div className="vben-card mb-4">
          <div className="vben-card-header">
            <h2 className="vben-card-title">选择恢复模式</h2>
          </div>
          <div className="vben-card-body space-y-3">
            <label
              className={`flex items-start gap-3 p-4 rounded-lg border cursor-pointer transition-colors ${
                restoreMode === 'all'
                  ? 'border-blue-300 bg-blue-50/60 dark:border-blue-700 dark:bg-blue-900/20'
                  : 'border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50'
              }`}
            >
              <input
                type="radio"
                name="restore-mode"
                checked={restoreMode === 'all'}
                onChange={() => setRestoreMode('all')}
                className="mt-0.5 w-4 h-4 accent-blue-500"
              />
              <div className="flex-1">
                <p className="font-medium text-sm text-slate-900 dark:text-slate-100">全部恢复</p>
                <p className="text-xs text-slate-500 mt-1">
                  恢复备份中的全部数据（系统账号密码除外）。适用于整库迁移。
                </p>
              </div>
            </label>

            <label
              className={`flex items-start gap-3 p-4 rounded-lg border cursor-pointer transition-colors ${
                restoreMode === 'shared'
                  ? 'border-blue-300 bg-blue-50/60 dark:border-blue-700 dark:bg-blue-900/20'
                  : 'border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50'
              }`}
            >
              <input
                type="radio"
                name="restore-mode"
                checked={restoreMode === 'shared'}
                onChange={() => setRestoreMode('shared')}
                className="mt-0.5 w-4 h-4 accent-blue-500"
              />
              <div className="flex-1">
                <p className="font-medium text-sm text-slate-900 dark:text-slate-100">恢复公用数据</p>
                <p className="text-xs text-slate-500 mt-1">
                  只恢复系统配置、卡券、商品素材、广告、分销财务等公用数据，
                  不包含闲鱼账号独有内容（关键词规则、默认回复、订单等）。
                </p>
              </div>
            </label>

            <label
              className={`flex items-start gap-3 p-4 rounded-lg border cursor-pointer transition-colors ${
                restoreMode === 'selected_accounts'
                  ? 'border-blue-300 bg-blue-50/60 dark:border-blue-700 dark:bg-blue-900/20'
                  : 'border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50'
              }`}
            >
              <input
                type="radio"
                name="restore-mode"
                checked={restoreMode === 'selected_accounts'}
                onChange={() => setRestoreMode('selected_accounts')}
                className="mt-0.5 w-4 h-4 accent-blue-500"
              />
              <div className="flex-1">
                <p className="font-medium text-sm text-slate-900 dark:text-slate-100">按账号恢复</p>
                <p className="text-xs text-slate-500 mt-1">
                  恢复公用数据 + 所选闲鱼账号的数据（账号配置、关键词规则、默认回复等）。
                  不影响目标环境中的其他账号。
                </p>
              </div>
            </label>
          </div>
        </div>

        {/* Account selection (按账号恢复模式) */}
        {restoreMode === 'selected_accounts' && (
          <div className="vben-card mb-4">
            <div className="vben-card-header">
              <h2 className="vben-card-title">选择要恢复的闲鱼账号</h2>
              <span className="badge-primary">
                {selectedAccountIds.size} / {parseResult.accounts.length} 已选
              </span>
            </div>
            <div className="vben-card-body">
              {parseResult.accounts.length === 0 ? (
                <div className="text-center py-6 text-slate-400 text-sm">
                  备份文件中没有找到闲鱼账号数据
                </div>
              ) : (
                <>
                  {/* Select all */}
                  <label className="flex items-center gap-3 p-3 rounded-lg bg-blue-50 dark:bg-blue-900/20 cursor-pointer hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-colors mb-3">
                    <input
                      type="checkbox"
                      checked={allAccountsSelected}
                      onChange={toggleAllAccounts}
                      className="w-4 h-4 rounded accent-blue-500"
                    />
                    <div>
                      <p className="font-semibold text-sm text-blue-700 dark:text-blue-400">全选</p>
                      <p className="text-xs text-blue-500/70">选择备份中的全部闲鱼账号</p>
                    </div>
                  </label>

                  <div className="space-y-2 max-h-[300px] overflow-y-auto">
                    {parseResult.accounts.map((acc) => (
                      <label
                        key={acc.account_id}
                        className={`flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
                          selectedAccountIds.has(acc.account_id)
                            ? 'border-blue-200 bg-blue-50/50 dark:border-blue-800 dark:bg-blue-900/10'
                            : 'border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={selectedAccountIds.has(acc.account_id)}
                          onChange={() => toggleAccount(acc.account_id)}
                          className="w-4 h-4 rounded accent-blue-500"
                        />
                        <div className="flex-1 min-w-0">
                          <p className="font-medium text-sm text-slate-900 dark:text-slate-100 truncate">
                            {acc.display_name || acc.account_id}
                          </p>
                          <p className="text-xs text-slate-500 truncate">
                            账号ID: {acc.account_id}
                            {acc.unb ? ` · UNB: ${acc.unb}` : ''}
                          </p>
                        </div>
                      </label>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {/* Warning */}
        <div className="p-4 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 flex items-start gap-3 mb-4">
          <ShieldAlert className="w-5 h-5 text-amber-600 dark:text-amber-400 flex-shrink-0 mt-0.5" />
          <div className="text-sm text-amber-700 dark:text-amber-400">
            <p className="font-medium">警告：恢复操作将覆盖当前数据库中的对应数据，此操作不可撤销。</p>
            <p className="mt-1">
              系统账号密码不会被恢复，当前登录态不受影响。闲鱼账号的 Cookie 可能已过期，
              恢复后账号需要重新登录刷新。
            </p>
          </div>
        </div>

        {/* Confirmation checkbox */}
        <label className="flex items-center gap-3 p-3 rounded-lg bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 cursor-pointer mb-4">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
            className="w-4 h-4 rounded accent-blue-500"
          />
          <span className="text-sm text-slate-700 dark:text-slate-300">
            我确认要执行数据库恢复操作，并了解这可能会覆盖现有数据
          </span>
        </label>

        {/* Actions */}
        <div className="flex gap-3">
          <button onClick={() => setStep('preview')} className="btn-ios-secondary">
            上一步
          </button>
          <button
            onClick={handleExecute}
            disabled={!canExecute}
            className="btn-ios-primary bg-red-600 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <RotateCcw className="w-4 h-4" />
            开始恢复
          </button>
        </div>
      </>
    )
  }

  // ==================== Step: executing ====================
  const renderExecuting = () => (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-white dark:bg-slate-800 rounded-2xl p-10 max-w-sm mx-4 text-center shadow-2xl">
        <div className="w-16 h-16 mx-auto mb-5 rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center">
          <Loader2 className="w-8 h-8 text-blue-600 dark:text-blue-400 animate-spin" />
        </div>
        <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-2">正在恢复数据库</h3>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          这可能需要几分钟时间，请勿关闭此页面...
        </p>
      </div>
    </div>
  )

  // ==================== Step: result ====================
  const renderResult = () => {
    if (executeError) {
      return (
        <>
          <div className="vben-card mb-4">
            <div className="vben-card-body">
              <div className="flex items-start gap-3 p-4 rounded-lg bg-red-50 dark:bg-red-900/20">
                <XCircle className="w-6 h-6 text-red-500 flex-shrink-0" />
                <div>
                  <p className="font-semibold text-red-700 dark:text-red-400">恢复执行失败</p>
                  <p className="text-sm text-red-600 dark:text-red-400 mt-1">{executeError}</p>
                </div>
              </div>
            </div>
          </div>
          <button onClick={resetAll} className="btn-ios-primary">
            <RefreshCw className="w-4 h-4" />
            返回重新操作
          </button>
        </>
      )
    }

    if (!executeResult) return null

    const hasFailures = executeResult.failed_tables.length > 0
    const allOk = !hasFailures && executeResult.restored_tables.length > 0

    return (
      <>
        {/* Summary */}
        <div className={`vben-card mb-4 ${allOk ? 'border-green-300 dark:border-green-700' : hasFailures ? 'border-amber-300 dark:border-amber-700' : ''}`}>
          <div className="vben-card-body">
            <div className="flex items-center gap-3 mb-4">
              {allOk ? (
                <CheckCircle className="w-8 h-8 text-green-500" />
              ) : (
                <AlertTriangle className="w-8 h-8 text-amber-500" />
              )}
              <div>
                <p className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                  {allOk ? '恢复完成' : '恢复完成（部分异常）'}
                </p>
                <div className="flex flex-wrap gap-x-4 gap-y-1 mt-1 text-sm text-slate-500">
                  <span>{executeResult.restored_tables.length} 张表已恢复</span>
                  <span>{executeResult.total_rows_inserted.toLocaleString()} 行数据已插入</span>
                  <span>耗时 {fmtDuration(executeResult.total_duration_ms)}</span>
                </div>
              </div>
            </div>

            {/* Account results (按账号恢复模式) */}
            {executeResult.account_results && (
              <div className="mb-4 p-3 rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800">
                <p className="font-medium text-blue-700 dark:text-blue-400 mb-2">账号恢复结果</p>
                <div className="space-y-1 text-sm">
                  <p className="text-slate-600 dark:text-slate-300">
                    已恢复账号：
                    <span className="font-medium">
                      {executeResult.account_results.restored_accounts.length > 0
                        ? executeResult.account_results.restored_accounts.join(', ')
                        : '无'}
                    </span>
                  </p>
                  {executeResult.account_results.missing_accounts.length > 0 && (
                    <p className="text-amber-600 dark:text-amber-400">
                      备份中未找到的账号：
                      <span className="font-medium">
                        {executeResult.account_results.missing_accounts.join(', ')}
                      </span>
                    </p>
                  )}
                </div>
              </div>
            )}

            {/* Failed tables */}
            {hasFailures && (
              <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
                <p className="font-medium text-red-700 dark:text-red-400 mb-2">恢复失败的表</p>
                <div className="space-y-1.5 max-h-[200px] overflow-y-auto">
                  {executeResult.failed_tables.map((f) => (
                    <div key={f.table} className="flex items-start gap-2 text-sm">
                      <XCircle className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" />
                      <div>
                        <span className="font-medium text-red-600">{f.table}</span>
                        <span className="text-red-500 ml-2">{f.error}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Restored tables */}
            {executeResult.restored_tables.length > 0 && (
              <details className="group">
                <summary className="cursor-pointer text-sm font-medium text-green-600 dark:text-green-400 hover:text-green-700 mb-2 select-none">
                  已恢复的表（{executeResult.restored_tables.length}）
                </summary>
                <div className="flex flex-wrap gap-1.5 max-h-[200px] overflow-y-auto">
                  {executeResult.restored_tables.map((t) => (
                    <span key={t} className="inline-block px-2 py-0.5 text-xs rounded bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 border border-green-200 dark:border-green-800">
                      {t}
                    </span>
                  ))}
                </div>
              </details>
            )}

            {/* Skipped tables */}
            {executeResult.skipped_tables.length > 0 && (
              <details className="group mt-3">
                <summary className="cursor-pointer text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-600 mb-2 select-none">
                  跳过的表（{executeResult.skipped_tables.length}）
                </summary>
                <p className="text-xs text-slate-400 mb-2">日志类表或不在选定分类内的表已跳过</p>
                <div className="flex flex-wrap gap-1.5 max-h-[150px] overflow-y-auto">
                  {executeResult.skipped_tables.map((t) => (
                    <span key={t} className="inline-block px-2 py-0.5 text-xs rounded bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400 border border-slate-200 dark:border-slate-600">
                      {t}
                    </span>
                  ))}
                </div>
              </details>
            )}
          </div>
        </div>

        <button onClick={resetAll} className="btn-ios-primary">
          <RefreshCw className="w-4 h-4" />
          开始新的恢复
        </button>
      </>
    )
  }

  // ==================== Main render ====================
  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="page-title">数据库恢复</h1>
          <p className="page-description">从备份文件恢复数据库，支持全部恢复、恢复公用数据、按账号恢复三种模式</p>
        </div>
        {step !== 'executing' && (
          <button onClick={resetAll} className="btn-ios-secondary">
            <RefreshCw className="w-4 h-4" />
            重新开始
          </button>
        )}
      </div>

      {/* Content by step */}
      {step !== 'result' && renderStepIndicator()}

      {step === 'select_source' && renderSelectSource()}
      {step === 'preview' && renderPreview()}
      {step === 'configure' && renderConfigure()}
      {step === 'executing' && renderExecuting()}
      {step === 'result' && renderResult()}

      {/* Preview modal */}
      <PreviewModal
        isOpen={showPreviewModal}
        result={previewResult}
        onClose={() => setShowPreviewModal(false)}
      />
    </div>
  )
}

// ==================== Sub-components ====================

/** Category card for preview step */
function CategoryCard({
  category,
  expanded,
  onToggleExpand,
}: {
  category: RestoreCategoryInfo
  expanded: boolean
  onToggleExpand: () => void
}) {
  return (
    <div className="p-3 rounded-lg border border-slate-200 dark:border-slate-700 hover:border-blue-200 dark:hover:border-blue-700 transition-colors">
      <div
        className="flex items-center justify-between cursor-pointer"
        onClick={onToggleExpand}
      >
        <div>
          <p className="font-medium text-sm text-slate-900 dark:text-slate-100">{category.label}</p>
          <p className="text-xs text-slate-500">{category.table_count} 张表</p>
        </div>
        {expanded ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
      </div>
      {expanded && (
        <div className="mt-2 space-y-1 max-h-[160px] overflow-y-auto">
          {category.tables.map((t) => (
            <div key={t.name} className="flex items-center gap-1.5 text-xs">
              <span className="font-mono text-slate-600 dark:text-slate-300">{t.name}</span>
              {t.has_data ? (
                <span className="px-1 py-0.5 text-[10px] rounded bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">含数据</span>
              ) : (
                <span className="px-1 py-0.5 text-[10px] rounded bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400">仅结构</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ==================== Preview Modal ====================

function PreviewModal({
  isOpen,
  result,
  onClose,
}: {
  isOpen: boolean
  result: PreviewResult | null
  onClose: () => void
}) {
  if (!isOpen || !result) return null

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative w-full max-w-3xl max-h-[85vh] mx-4 bg-white dark:bg-slate-800 rounded-2xl shadow-2xl border border-slate-200 dark:border-slate-700 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-5 pb-4 border-b border-slate-200 dark:border-slate-700">
          <div>
            <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
              备份预览
            </h3>
            <p className="text-sm text-slate-500 mt-0.5 truncate max-w-md" title={result.source_file}>
              {result.source_file}
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Summary bar */}
        <div className="flex flex-wrap gap-x-6 gap-y-1 px-5 py-3 bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700 text-sm">
          <span className="text-slate-600 dark:text-slate-400">
            文件大小: <span className="font-medium text-slate-900 dark:text-slate-200">{result.file_size_formatted}</span>
          </span>
          <span className="text-slate-600 dark:text-slate-400">
            总行数: <span className="font-medium text-blue-600 dark:text-blue-400">{result.total_rows.toLocaleString()}</span>
          </span>
          <span className="text-slate-600 dark:text-slate-400">
            分类数: <span className="font-medium text-slate-900 dark:text-slate-200">{result.categories.length}</span>
          </span>
        </div>

        {/* Category cards grid (scrollable) */}
        <div className="flex-1 overflow-y-auto p-5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {result.categories.map((cat) => (
              <div
                key={cat.key}
                className="p-3 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800"
              >
                {/* Category header */}
                <div className="flex items-center justify-between mb-2">
                  <p className="font-medium text-sm text-slate-900 dark:text-slate-100">
                    {cat.label}
                  </p>
                  <span className="text-xs px-2 py-0.5 rounded-full bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400 font-medium">
                    {cat.total_rows.toLocaleString()} 行
                  </span>
                </div>
                <p className="text-xs text-slate-400 mb-2">{cat.table_count} 张表</p>

                {/* Table list */}
                <div className="space-y-0.5 max-h-[200px] overflow-y-auto">
                  {cat.tables.map((t) => (
                    <div
                      key={t.name}
                      className={`flex items-center justify-between text-xs py-1 px-1.5 rounded hover:bg-slate-50 dark:hover:bg-slate-700/50 ${
                        t.rows === 0 ? 'opacity-50' : ''
                      }`}
                    >
                      <div className="flex items-center gap-1.5 min-w-0">
                        {t.label ? (
                          <>
                            <span className="text-slate-700 dark:text-slate-300 truncate font-medium" title={t.label}>
                              {t.label}
                            </span>
                            <span className="text-[10px] text-slate-400 truncate hidden sm:inline" title={t.name}>
                              ({t.name})
                            </span>
                          </>
                        ) : (
                          <span className="font-mono text-slate-500 dark:text-slate-400 truncate" title={t.name}>
                            {t.name}
                          </span>
                        )}
                      </div>
                      <span className="flex-shrink-0 text-slate-500 dark:text-slate-400 ml-2 tabular-nums">
                        {t.rows.toLocaleString()} 行
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Footer */}
        <div className="px-5 py-4 border-t border-slate-200 dark:border-slate-700 flex justify-end">
          <button
            onClick={onClose}
            className="px-5 py-2 rounded-lg font-medium transition-colors text-sm bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-600"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
