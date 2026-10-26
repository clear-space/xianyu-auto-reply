/**
 * 登录页科技感背景
 *
 * 纯 CSS 分层实现，无外部图片：
 * 1. 渐变底色（浅色：雾蓝科技；深色：深空蓝紫）
 * 2. 网格线（中心向四周渐隐，营造数据网络感）
 * 3. 三个缓慢漂浮的光斑（蓝/靛/青）
 * 4. 顶部一条贯穿的辉光细线
 *
 * 用法：放在父容器（relative overflow-hidden）内，作为第一个子元素
 */
export function TechBackground() {
  return (
    <div aria-hidden className="absolute inset-0 overflow-hidden pointer-events-none">
      {/* 1. 渐变底色 */}
      <div className="absolute inset-0 bg-gradient-to-br from-sky-100 via-blue-50 to-indigo-100 dark:from-[#050b1f] dark:via-[#081234] dark:to-[#0b1c45]" />

      {/* 2. 网格线 */}
      <div className="absolute inset-0 tech-grid" />

      {/* 3. 漂浮光斑 */}
      <div className="absolute -top-32 -left-24 w-96 h-96 rounded-full bg-blue-400/40 dark:bg-cyan-400/20 blur-3xl tech-orb-1" />
      <div className="absolute top-1/3 -right-24 w-[28rem] h-[28rem] rounded-full bg-indigo-400/35 dark:bg-blue-600/25 blur-3xl tech-orb-2" />
      <div className="absolute -bottom-32 left-1/3 w-96 h-96 rounded-full bg-cyan-300/35 dark:bg-indigo-500/20 blur-3xl tech-orb-3" />

      {/* 4. 顶部辉光细线 */}
      <div className="absolute top-0 left-1/4 right-1/4 h-px bg-gradient-to-r from-transparent via-blue-400/60 dark:via-cyan-300/50 to-transparent" />
    </div>
  )
}
