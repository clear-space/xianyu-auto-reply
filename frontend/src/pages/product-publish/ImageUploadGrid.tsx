/**
 * 商品图片上传网格。
 * 支持拖拽调整图片顺序，第一张图片作为首图（展示「首图」标记）。
 * 文件上传逻辑由父组件负责，本组件仅负责选择文件并回调。
 */
import React, { useRef, useState } from 'react'
import { Loader2, Trash2, Upload } from 'lucide-react'

export interface ImageUploadGridProps {
  /** 图片地址列表，顺序即发布顺序，第一张为首图 */
  images: string[]
  /** 图片顺序变化（拖拽排序、移除）时回调 */
  onChange: (images: string[]) => void
  /** 用户选择文件后回调，由父组件负责上传并追加图片 */
  onUpload: (files: File[]) => void
  /** 是否正在上传（显示加载态并禁用添加按钮） */
  uploading?: boolean
  /** 最大图片数量 */
  max?: number
  /** 是否在第一张图上显示「首图」标记 */
  showFirstBadge?: boolean
  /** 自定义提示文案（显示在图片下方）；null 表示不显示提示 */
  hint?: string | null
  /** 禁用添加图片按钮 */
  disabled?: boolean
}

export function ImageUploadGrid({ images, onChange, onUpload, uploading = false, max = 9, showFirstBadge = true, hint, disabled = false }: ImageUploadGridProps) {
  /** 未传 hint 时：多张图显示默认拖拽提示 */
  const hintText = hint !== undefined ? hint : images.length > 1 ? '拖拽图片可调整顺序，第一张图片将作为首图展示' : null
  const fileInputRef = useRef<HTMLInputElement>(null)
  /** 正在拖拽的图片下标 */
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  /** 插入位置：插到该下标图片之前；等于 images.length 表示插到末尾 */
  const [dropTarget, setDropTarget] = useState<number | null>(null)

  const resetDrag = () => {
    setDragIndex(null)
    setDropTarget(null)
  }

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    if (files.length) onUpload(files)
  }

  const removeImage = (index: number) => onChange(images.filter((_, itemIndex) => itemIndex !== index))

  /** 按指针在图片左/右半区计算插入下标 */
  const resolveDropTarget = (index: number, event: React.DragEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    return event.clientX > rect.left + rect.width / 2 ? index + 1 : index
  }

  /** 提交排序：把 from 移到 target 之前 */
  const commitReorder = (from: number, target: number) => {
    resetDrag()
    // 拖回原位或紧邻自身右侧视为未移动
    if (target === from || target === from + 1) return
    const next = [...images]
    const [moved] = next.splice(from, 1)
    const insertAt = target > from ? target - 1 : target
    next.splice(insertAt, 0, moved)
    onChange(next)
  }

  const handleDragStart = (index: number, event: React.DragEvent<HTMLDivElement>) => {
    setDragIndex(index)
    event.dataTransfer.effectAllowed = 'move'
    // Firefox 需要 setData 才会启动拖拽
    event.dataTransfer.setData('text/plain', String(index))
  }

  const handleDragOver = (index: number, event: React.DragEvent<HTMLDivElement>) => {
    if (dragIndex === null) return
    event.preventDefault()
    event.stopPropagation()
    event.dataTransfer.dropEffect = 'move'
    const target = resolveDropTarget(index, event)
    // 落在自身或紧邻自身右侧不显示插入指示
    setDropTarget(target === dragIndex || target === dragIndex + 1 ? null : target)
  }

  const handleDrop = (index: number, event: React.DragEvent<HTMLDivElement>) => {
    if (dragIndex === null) return
    event.preventDefault()
    event.stopPropagation()
    commitReorder(dragIndex, resolveDropTarget(index, event))
  }

  /** 容器空白处拖过/放下：按插到末尾处理 */
  const handleContainerDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    if (dragIndex === null) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    setDropTarget(images.length)
  }

  const handleContainerDrop = (event: React.DragEvent<HTMLDivElement>) => {
    if (dragIndex === null) return
    event.preventDefault()
    commitReorder(dragIndex, images.length)
  }

  return (
    <div>
      <div className="flex flex-wrap gap-2" onDragOver={handleContainerDragOver} onDrop={handleContainerDrop}>
        {images.map((url, index) => (
          <div
            key={url}
            draggable
            onDragStart={(event) => handleDragStart(index, event)}
            onDragOver={(event) => handleDragOver(index, event)}
            onDrop={(event) => handleDrop(index, event)}
            onDragEnd={resetDrag}
            className={`group relative h-20 w-20 cursor-grab select-none overflow-hidden rounded-lg border border-slate-200 dark:border-slate-600 ${dragIndex === index ? 'opacity-40' : ''}`}
          >
            <img src={url} alt={`商品图${index + 1}`} draggable={false} className="pointer-events-none h-full w-full object-cover" />
            {index === 0 && showFirstBadge && (
              <span className="absolute bottom-0 left-0 right-0 bg-blue-500/80 py-0.5 text-center text-[10px] text-white">首图</span>
            )}
            {/* 插入位置指示条 */}
            {dropTarget === index && <span className="pointer-events-none absolute inset-y-0 left-0 w-1 bg-blue-500" />}
            {dropTarget === images.length && index === images.length - 1 && (
              <span className="pointer-events-none absolute inset-y-0 right-0 w-1 bg-blue-500" />
            )}
            <button
              type="button"
              title="移除图片"
              onClick={() => removeImage(index)}
              className="absolute right-0.5 top-0.5 rounded bg-black/60 p-0.5 text-white opacity-0 transition-opacity group-hover:opacity-100 hover:bg-red-500"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </div>
        ))}
        {images.length < max && (
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading || disabled}
            className="flex h-20 w-20 flex-col items-center justify-center rounded-lg border-2 border-dashed border-slate-300 text-slate-400 transition-colors hover:border-blue-400 hover:text-blue-500 disabled:opacity-50 dark:border-slate-600"
          >
            {uploading ? <Loader2 className="h-5 w-5 animate-spin" /> : <Upload className="h-5 w-5" />}
            <span className="mt-1 text-xs">{uploading ? '上传中' : '添加图片'}</span>
          </button>
        )}
        <input ref={fileInputRef} type="file" accept="image/*" multiple className="hidden" onChange={handleFileChange} />
      </div>
      {hintText && (
        <p className="mt-2 text-xs text-slate-400">{hintText}</p>
      )}
    </div>
  )
}

export default ImageUploadGrid
