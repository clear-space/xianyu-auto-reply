/**
 * 分段选择控件（iOS 风格胶囊分段）
 *
 * 用于表单里的互斥选项（重复模式/时间模式/发布模式等），
 * 统一替代各处手写的分段按钮样式。
 */
export interface SegmentedOption<T extends string> {
  value: T
  label: string
  /** 副标题（可选，如发布模式的两行说明） */
  desc?: string
}

interface Props<T extends string> {
  options: SegmentedOption<T>[]
  value: T
  onChange: (v: T) => void
  className?: string
}

export function SegmentedControl<T extends string>({ options, value, onChange, className = '' }: Props<T>) {
  return (
    <div className={`inline-flex w-full bg-slate-100 dark:bg-slate-800 rounded-lg p-1 gap-1 ${className}`}>
      {options.map(o => {
        const active = o.value === value
        return (
          <button key={o.value} type="button" onClick={() => onChange(o.value)}
            className={`flex-1 px-3 py-1.5 rounded-md text-sm transition-colors ${
              active
                ? 'bg-white dark:bg-slate-700 shadow-sm text-blue-600 font-medium'
                : 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200'
            }`}>
            <span className="block">{o.label}</span>
            {o.desc && (
              <span className={`block text-xs mt-0.5 ${active ? 'text-blue-500/80' : 'opacity-60'}`}>{o.desc}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}

export default SegmentedControl
