/**
 * 手动清理弹窗（系统信息看板）
 *
 * 三态流程：
 * 1. confirm —— 勾选要执行的清理项（含范围与保留策略说明），默认勾选除「备份并清理」外的全部
 * 2. running —— 逐项顺序触发（调度器内部串行执行），展示每项状态（排队/执行中/完成/失败）
 * 3. result  —— 展示清理结果：数据库逐表删除行数审计 + 最近一次备份结果 + 目录体积已刷新提示
 *
 * 说明：任务触发接口会等待调度器执行完毕才返回，因此触发完成后审计数据已落库，直接拉取即可。
 */

import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  CheckCircle2,
  Loader2,
  Sparkles,
  X,
  XCircle,
} from 'lucide-react'
import { triggerScheduledTask } from '@/api/scheduledTasks'
import { getCleanupReport, refreshDirs, type CleanupReport } from '@/api/systemInfo'
import { useUIStore } from '@/store/uiStore'

// ==================== 清理项定义 ====================

export interface CleanupItem {
  key: string
  label: string
  desc: string
  taskCode: string
  defaultChecked: boolean
  hasReport: boolean
}

// 一键清理弹窗中的清理项（与存储分布表格的单类清理共用任务代码）
export const CLEANUP_ITEMS: CleanupItem[] = [
  {
    key: 'db',
    label: '数据库日志数据',
    desc: '按保留策略删除超期记录（默认30天）',
    taskCode: 'data_retention_cleanup',
    defaultChecked: true,
    hasReport: true,
  },
  {
    key: 'files',
    label: '上传文件',
    desc: '清理孤儿/超期图片（卡券/素材/关键词/回复/人脸截图/公开媒体）',
    taskCode: 'image_cleanup',
    defaultChecked: true,
    hasReport: false,
  },
  {
    key: 'browser',
    label: '浏览器数据',
    desc: '仅清理禁用超10天账号的数据（启用账号登录态保留）',
    taskCode: 'cleanup_browser_data',
    defaultChecked: true,
    hasReport: false,
  },
  {
    key: 'temp',
    label: '过期临时文件',
    desc: '滑块会话/发布图片/恢复上传残留',
    taskCode: 'stale_temp_cleanup',
    defaultChecked: true,
    hasReport: false,
  },
  {
    key: 'backup',
    label: '备份并清理',
    desc: '先执行一次全库备份再删过期备份（预计1~5分钟）',
    taskCode: 'db_backup',
    defaultChecked: false,
    hasReport: false,
  },
]

type ItemStatus = 'pending' | 'running' | 'done' | 'failed'

interface CleanupModalProps {
  isOpen: boolean
  onClose: () => void
  /** 清理完成并刷新数据后的回调（父组件刷新存储/摘要等区块） */
  onFinished: () => void
}

export function CleanupModal({ isOpen, onClose, onFinished }: CleanupModalProps) {
  const { addToast } = useUIStore()
  const [phase, setPhase] = useState<'confirm' | 'running' | 'result'>('confirm')
  const [checked, setChecked] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(CLEANUP_ITEMS.map((item) => [item.key, item.defaultChecked])),
  )
  const [itemStatus, setItemStatus] = useState<Record<string, ItemStatus>>(() =>
    Object.fromEntries(CLEANUP_ITEMS.map((item) => [item.key, 'pending'])),
  )
  const [report, setReport] = useState<CleanupReport | null>(null)
  const finishedRef = useRef(false)

  // 弹窗打开时重置状态
  useEffect(() => {
    if (isOpen) {
      setPhase('confirm')
      setChecked(Object.fromEntries(CLEANUP_ITEMS.map((item) => [item.key, item.defaultChecked])))
      setItemStatus(Object.fromEntries(CLEANUP_ITEMS.map((item) => [item.key, 'pending'])))
      setReport(null)
      finishedRef.current = false
    }
  }, [isOpen])

  const selectedItems = CLEANUP_ITEMS.filter((item) => checked[item.key])

  const handleStart = async () => {
    setPhase('running')
    let anySucceeded = false
    let anyFailed = false

    for (const item of selectedItems) {
      setItemStatus((s) => ({ ...s, [item.key]: 'running' }))
      try {
        const result = await triggerScheduledTask(item.taskCode)
        if (result.success) {
          setItemStatus((s) => ({ ...s, [item.key]: 'done' }))
          anySucceeded = true
        } else {
          throw new Error(result.message || '触发失败')
        }
      } catch (e: any) {
        setItemStatus((s) => ({ ...s, [item.key]: 'failed' }))
        anyFailed = true
        addToast({ type: 'error', message: `${item.label}清理失败：${e.message || '请检查调度服务是否在线'}` })
      }
    }

    // 数据库项触发过 → 拉取审计报告；其余项完成后刷新目录体积缓存
    const dbSelected = checked['db']
    try {
      if (dbSelected) {
        setReport(await getCleanupReport())
      }
    } catch {
      // 报告拉取失败不影响主流程
    }
    try {
      await refreshDirs()
    } catch {
      // 刷新失败不影响主流程
    }

    if (anyFailed && !anySucceeded) {
      // 全部失败：给出明确提示并留在运行态展示失败项
      addToast({ type: 'error', message: '所有清理项均执行失败，请检查调度服务是否在线' })
    } else if (anyFailed) {
      addToast({ type: 'warning', message: '部分清理项执行失败，详情见弹窗' })
    } else {
      addToast({ type: 'success', message: '清理已完成' })
    }

    finishedRef.current = true
    setPhase('result')
  }

  const handleDone = () => {
    onClose()
    if (finishedRef.current) {
      onFinished()
    }
  }

  return (
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 bg-black/50 backdrop-blur-sm"
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            transition={{ duration: 0.15 }}
            className="relative w-[520px] max-w-[92vw] max-h-[82vh] overflow-y-auto rounded-xl bg-white dark:bg-gray-800 shadow-2xl"
          >
            {/* 头部 */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-slate-700">
              <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-blue-500" />
                {phase === 'confirm' ? '一键清理' : phase === 'running' ? '清理执行中' : '清理结果'}
              </h3>
              {phase !== 'running' && (
                <button onClick={onClose} className="p-1 text-slate-400 hover:text-slate-600">
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>

            {/* 内容 */}
            <div className="px-5 py-4">
              {phase === 'confirm' && (
                <>
                  <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">
                    将按当前保留策略执行以下清理，<span className="text-slate-700 dark:text-slate-300">只删超期数据，不动活跃数据</span>：
                  </p>
                  <div className="space-y-2.5">
                    {CLEANUP_ITEMS.map((item) => (
                      <label
                        key={item.key}
                        className="flex items-start gap-2.5 p-2.5 rounded-lg border border-slate-100 dark:border-slate-700 cursor-pointer hover:bg-slate-50 dark:hover:bg-gray-700/50"
                      >
                        <input
                          type="checkbox"
                          checked={checked[item.key] ?? false}
                          onChange={(e) => setChecked((c) => ({ ...c, [item.key]: e.target.checked }))}
                          className="mt-0.5"
                        />
                        <div className="flex-1">
                          <p className="text-sm font-medium text-slate-800 dark:text-slate-200">{item.label}</p>
                          <p className="text-xs text-slate-500 dark:text-slate-400">{item.desc}</p>
                        </div>
                      </label>
                    ))}
                  </div>
                  <div className="flex justify-end gap-2 mt-5">
                    <button onClick={onClose} className="btn-ios-secondary">取消</button>
                    <button
                      onClick={handleStart}
                      disabled={selectedItems.length === 0}
                      className="btn-ios-primary disabled:opacity-40"
                    >
                      开始清理
                    </button>
                  </div>
                </>
              )}

              {phase === 'running' && (
                <div className="space-y-2.5">
                  {selectedItems.map((item) => {
                    const status = itemStatus[item.key]
                    return (
                      <div key={item.key} className="flex items-center gap-2.5 p-2.5 rounded-lg border border-slate-100 dark:border-slate-700">
                        <span className="w-5 flex justify-center">
                          {status === 'running' && <Loader2 className="w-4 h-4 animate-spin text-blue-500" />}
                          {status === 'done' && <CheckCircle2 className="w-4 h-4 text-emerald-500" />}
                          {status === 'failed' && <XCircle className="w-4 h-4 text-red-500" />}
                          {status === 'pending' && <span className="w-4 h-4 rounded-full border-2 border-slate-200" />}
                        </span>
                        <div className="flex-1">
                          <p className="text-sm text-slate-800 dark:text-slate-200">{item.label}</p>
                          <p className="text-xs text-slate-500 dark:text-slate-400">
                            {status === 'pending' && '排队中...'}
                            {status === 'running' && '执行中（完成后自动进行下一项）...'}
                            {status === 'done' && '已完成'}
                            {status === 'failed' && '执行失败'}
                          </p>
                        </div>
                      </div>
                    )
                  })}
                  <p className="text-xs text-slate-400 pt-1">
                    清理任务在调度服务中执行，大数据量时数据库项可能需要数十秒，请勿关闭弹窗。
                  </p>
                </div>
              )}

              {phase === 'result' && (
                <div className="space-y-3">
                  {/* 逐项状态 */}
                  <div className="space-y-1.5">
                    {selectedItems.map((item) => (
                      <div key={item.key} className="flex items-center gap-2 text-sm">
                        {itemStatus[item.key] === 'done' ? (
                          <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                        ) : (
                          <XCircle className="w-4 h-4 text-red-500" />
                        )}
                        <span className="text-slate-700 dark:text-slate-300">{item.label}</span>
                        <span className="text-xs text-slate-400">
                          {itemStatus[item.key] === 'done' ? '已执行' : '失败'}
                        </span>
                      </div>
                    ))}
                  </div>

                  {/* 数据库审计明细 */}
                  {checked['db'] && itemStatus['db'] === 'done' && (
                    <div className="rounded-lg border border-slate-100 dark:border-slate-700 p-3">
                      <p className="text-sm font-medium text-slate-800 dark:text-slate-200 mb-2">
                        数据库本轮共删除 {report?.total_deleted?.toLocaleString() ?? 0} 行
                      </p>
                      <div className="max-h-44 overflow-y-auto">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="text-left text-slate-400 border-b border-slate-100 dark:border-slate-700">
                              <th className="py-1">表名</th>
                              <th className="text-right">删除行数</th>
                              <th className="text-right">状态</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(report?.tables ?? []).map((t) => (
                              <tr key={t.table_name} className="border-b border-slate-50 dark:border-slate-800">
                                <td className="py-1 text-slate-700 dark:text-slate-300">{t.table_name}</td>
                                <td className="text-right">{t.deleted_rows.toLocaleString()}</td>
                                <td className="text-right">
                                  {t.status === 'success' ? (
                                    <span className="text-emerald-600">✓</span>
                                  ) : (
                                    <span className="text-red-500">✗</span>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* 备份结果 */}
                  {checked['backup'] && itemStatus['backup'] === 'done' && (
                    <p className="text-xs text-slate-500">
                      最近备份：
                      {report?.last_backup
                        ? `${report.last_backup.status === 'success' ? '成功' : '失败'}（${report.last_backup.file_name ?? '-'}）`
                        : '暂无备份记录'}
                    </p>
                  )}

                  {!checked['db'] && (
                    <p className="text-xs text-slate-400">文件类清理无逐项审计，存储分布数字已刷新。</p>
                  )}

                  <div className="flex justify-end pt-2">
                    <button onClick={handleDone} className="btn-ios-primary">完成</button>
                  </div>
                </div>
              )}
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  )
}
