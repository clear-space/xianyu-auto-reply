/**
 * 数据罗盘指标定义（基于闲鱼官方接口实调返回的 36 个 banner 指标 + 图表字段）
 *
 * 来源：mtop.alibaba.idle.seller.pc.datacompass.singleuser.seller.summary 实调验证
 * 说明：
 * - banner 的 dataStr 已由官方格式化（百分比/金额/数量），前端直接展示，勿自行换算
 * - 图表原始值为 number，需按 format 类型换算（percent 类字段官方返回小数如 0.0599）
 */

import type { BannerDataItem } from '@/api/data_analysis'

/** 指标数值格式（用于趋势图 Y 轴与 Tooltip 换算） */
export type MetricFormat = 'number' | 'percent' | 'currency'

export interface MetricDef {
  name: string
  label: string
  format: MetricFormat
  /** 复合卡：从 banner.extendInfo 里拆分子指标 */
  composite?: string[]
  /** 竞争力指标：dataStr 为百分位，卡片加「超越 xx% 同行」角标 */
  competitive?: boolean
  /** 名称存在推测成分（官方无文档，按字段语义推断） */
  uncertain?: boolean
}

/** 指标分组 */
export interface MetricGroupDef {
  key: string
  label: string
  metrics: MetricDef[]
}

/** 总览页五大分组（官方 banner 顺序重组） */
export const OVERVIEW_GROUPS: MetricGroupDef[] = [
  {
    key: 'trade',
    label: '成交数据',
    metrics: [
      { name: 'payAmt', label: '支付金额（元）', format: 'currency', composite: ['fstByrPayAmt', 'rptByrPayAmt'] },
      { name: 'fstByrPayAmt', label: '首次支付金额（元）', format: 'currency' },
      { name: 'rptByrPayAmt', label: '复购支付金额（元）', format: 'currency' },
      { name: 'payOrdCnt', label: '支付笔数', format: 'number' },
      { name: 'payByrCnt', label: '支付买家数', format: 'number' },
      { name: 'aov', label: '客单价（元）', format: 'currency' },
      { name: 'rfdOrdCnt', label: '退款笔数', format: 'number' },
      { name: 'rfdAmt', label: '退款金额（元）', format: 'currency' },
    ],
  },
  {
    key: 'flow',
    label: '流量数据',
    metrics: [
      { name: 'showPv', label: '曝光次数', format: 'number' },
      { name: 'showUv', label: '曝光人数', format: 'number' },
      { name: 'ipv', label: '浏览次数', format: 'number' },
      { name: 'ipvUv', label: '浏览人数', format: 'number' },
      { name: 'vstPv', label: '访问次数', format: 'number' },
      { name: 'vstUv', label: '访问人数', format: 'number' },
      { name: 'showItmCnt', label: '曝光商品数', format: 'number' },
      { name: 'ipvItmCnt', label: '访问商品数', format: 'number' },
      { name: 'uctr', label: '访问转化率', format: 'percent' },
      { name: 'uctcvr', label: '浏览支付转化率', format: 'percent' },
    ],
  },
  {
    key: 'repurchase',
    label: '复购数据',
    metrics: [
      { name: 'rptOrdCnt', label: '复购订单数', format: 'number' },
      { name: 'rptByrCnt', label: '复购买家数', format: 'number' },
      { name: 'rpr', label: '复购率', format: 'percent' },
    ],
  },
  {
    key: 'item',
    label: '商品运营',
    metrics: [
      { name: 'onlCnt', label: '在架商品数', format: 'number' },
      { name: 'stItmCnt', label: '成交商品数', format: 'number' },
      { name: 'newItmCnt', label: '新发商品数', format: 'number' },
      { name: 'favCnt', label: '收藏数', format: 'number' },
      { name: 'chatUv', label: '咨询人数', format: 'number' },
      { name: 'cmtItmCnt', label: '评价商品数', format: 'number' },
      { name: 'addRecItemCnt', label: '加入推荐商品数', format: 'number' },
      { name: 'priceCutItmCnt', label: '降价商品数', format: 'number' },
      { name: 'rep3minUvRate', label: '3分钟回复率', format: 'percent' },
      { name: 'rptNewItmCntYx', label: '上新优选数', format: 'number', uncertain: true },
      { name: 'newItmCntYx', label: '优选商品数', format: 'number', uncertain: true },
      { name: 'actCnt', label: '活动商品数', format: 'number', uncertain: true },
      { name: 'dftCnt', label: '草稿商品数', format: 'number', uncertain: true },
      { name: 'onSaleStk', label: '在售库存', format: 'number', uncertain: true },
    ],
  },
  {
    key: 'competitive',
    label: '同行竞争力',
    metrics: [
      { name: 'showPvCmpPctl', label: '曝光竞争力', format: 'percent', competitive: true },
      { name: 'payOrdCntCmpPctl', label: '成交竞争力', format: 'percent', competitive: true },
    ],
  },
]

/** 全部指标定义（含图表专属字段），name → def */
export const METRIC_DEFS: Record<string, MetricDef> = Object.fromEntries(
  OVERVIEW_GROUPS.flatMap((g) => g.metrics).map((m) => [m.name, m]),
)

/* ============ 经营数据页各模块指标（复购/粉丝/客服，均经实调验证） ============ */

/** 商品概览（item.summary 实调返回的 14 个商品维度指标） */
export const ITEM_SUMMARY_GROUPS: MetricGroupDef[] = [
  {
    key: 'item',
    label: '商品概览',
    metrics: [
      { name: 'onlCnt', label: '在架商品数', format: 'number' },
      { name: 'stItmCnt', label: '成交商品数', format: 'number' },
      { name: 'ipvItmCnt', label: '访问商品数', format: 'number' },
      { name: 'newItmCnt', label: '新发商品数', format: 'number' },
      { name: 'rptNewItmCntYx', label: '上新优选数', format: 'number', uncertain: true },
      { name: 'newItmCntYx', label: '优选商品数', format: 'number', uncertain: true },
      { name: 'favCnt', label: '收藏数', format: 'number' },
      { name: 'chatUv', label: '咨询人数', format: 'number' },
      { name: 'cmtItmCnt', label: '评价商品数', format: 'number' },
      { name: 'dftCnt', label: '草稿商品数', format: 'number', uncertain: true },
      { name: 'rptOrdCnt', label: '复购订单数', format: 'number' },
      { name: 'rptByrCnt', label: '复购买家数', format: 'number' },
      { name: 'rpr', label: '复购率', format: 'percent' },
      { name: 'rfdOrdCnt', label: '退款笔数', format: 'number' },
    ],
  },
]

/** 复购概览（repurchase.summary 实调返回的 10 个指标，无趋势图数据） */
export const REPURCHASE_GROUPS: MetricGroupDef[] = [
  {
    key: 'repurchase',
    label: '复购指标',
    metrics: [
      { name: 'rptOrdCnt', label: '复购订单数', format: 'number' },
      { name: 'rptByrCnt', label: '复购买家数', format: 'number' },
      { name: 'rpr', label: '复购率', format: 'percent' },
      { name: 'newItmCnt', label: '新发商品数', format: 'number' },
      { name: 'rptNewItmCntYx', label: '上新优选数', format: 'number', uncertain: true },
      { name: 'newItmCntYx', label: '优选商品数', format: 'number', uncertain: true },
      { name: 'chatUv', label: '咨询人数', format: 'number' },
      { name: 'favCnt', label: '收藏数', format: 'number' },
      { name: 'cmtItmCnt', label: '评价商品数', format: 'number' },
      { name: 'dftCnt', label: '草稿商品数', format: 'number', uncertain: true },
    ],
  },
]

/** 粉丝概况（fans.summary：3 banner + 5 图表字段） */
export const FANS_GROUPS: MetricGroupDef[] = [
  {
    key: 'fans',
    label: '粉丝概况',
    metrics: [
      { name: 'totalFansCnt', label: '粉丝总数', format: 'number' },
      { name: 'newFansCnt', label: '新增粉丝数', format: 'number' },
      { name: 'fansOrdRatio', label: '粉丝下单占比', format: 'percent' },
      { name: 'fansPayOrdCnt', label: '粉丝支付订单数', format: 'number' },
      { name: 'totalPayOrdCnt', label: '全部支付订单数', format: 'number' },
    ],
  },
]

/** 客服概览（cs.overview.summary：14 banner，9 个图表字段均在其中） */
export const CS_GROUPS: MetricGroupDef[] = [
  {
    key: 'service',
    label: '服务指标',
    metrics: [
      { name: 'consultUv', label: '咨询人数', format: 'number' },
      { name: 'manualSessCnt', label: '人工会话数', format: 'number' },
      { name: 'consultItmCnt', label: '咨询商品数', format: 'number' },
      { name: 'visitorCnt', label: '访客数', format: 'number' },
      { name: 'respRate3min', label: '3分钟回复率', format: 'percent' },
      { name: 'respRate1min', label: '1分钟回复率', format: 'percent' },
      { name: 'custSatRate', label: '客户满意度', format: 'percent' },
      { name: 'consultRate', label: '咨询率', format: 'percent' },
      { name: 'chatCvr', label: '聊天转化率', format: 'percent' },
      { name: 'avgRespTm', label: '平均响应时长（秒）', format: 'number' },
      { name: 'firstAvgRespTm', label: '首次平均响应时长（秒）', format: 'number' },
      { name: 'avgCumWorkHrs', label: '平均累计工作时长', format: 'number', uncertain: true },
      { name: 'csSalesAmt', label: '客服成交金额（元）', format: 'currency' },
      { name: 'csSalesByrCnt', label: '客服成交买家数', format: 'number' },
    ],
  },
]

/** 退款指标定义（refund.summary 实调返回的 34 个指标）
 *  kind: amount = 裸数值（金额，元）；item = BannerDataItem 结构（笔数/比率） */
export interface RefundMetricDef {
  name: string
  label: string
  kind: 'amount' | 'item'
  uncertain?: boolean
}

export interface RefundGroupDef {
  key: string
  label: string
  metrics: RefundMetricDef[]
}

export const REFUND_GROUPS: RefundGroupDef[] = [
  {
    key: 'current',
    label: '当前退款',
    metrics: [
      { name: 'crtRfdAmt', label: '当前退款金额', kind: 'amount' },
      { name: 'crtRfdCnt', label: '当前退款笔数', kind: 'item' },
      { name: 'crtRfdRate', label: '当前退款率', kind: 'item' },
      { name: 'dealingRfdAmt', label: '处理中退款金额', kind: 'amount', uncertain: true },
      { name: 'dealingRfdCnt', label: '处理中退款笔数', kind: 'item', uncertain: true },
      { name: 'dealingRfdCntRate', label: '处理中退款笔数率', kind: 'item', uncertain: true },
    ],
  },
  {
    key: 'stage',
    label: '发货前/后',
    metrics: [
      { name: 'bfCsnRfdAmt', label: '发货前退款金额', kind: 'amount' },
      { name: 'bfCsnRfdCnt', label: '发货前退款笔数', kind: 'item' },
      { name: 'afCsnRfdAmt', label: '发货后退款金额', kind: 'amount' },
      { name: 'afCsnRfdCnt', label: '发货后退款笔数', kind: 'item' },
      { name: 'afSignRfdAmt', label: '签收后退款金额', kind: 'amount', uncertain: true },
      { name: 'afSignRfdCnt', label: '签收后退款笔数', kind: 'item', uncertain: true },
    ],
  },
  {
    key: 'type',
    label: '退款类型',
    metrics: [
      { name: 'onlyRfdAmt', label: '仅退款金额', kind: 'amount' },
      { name: 'onlyRfdCnt', label: '仅退款笔数', kind: 'item' },
      { name: 'rtnRfdAmt', label: '退货退款金额', kind: 'amount' },
      { name: 'rtnRfdCnt', label: '退货退款笔数', kind: 'item' },
    ],
  },
  {
    key: 'dispute',
    label: '纠纷与平台介入',
    metrics: [
      { name: 'crtDsptAmt', label: '当前纠纷金额', kind: 'amount' },
      { name: 'crtDsptCnt', label: '当前纠纷笔数', kind: 'item' },
      { name: 'crtDsptRate', label: '当前纠纷率', kind: 'item' },
      { name: 'platCrtOnlyRfdAmt', label: '平台介入仅退款金额', kind: 'amount' },
      { name: 'platCrtOnlyRfdCnt', label: '平台介入仅退款笔数', kind: 'item' },
      { name: 'platCrtRtnRfdAmt', label: '平台介入退货退款金额', kind: 'amount' },
      { name: 'platCrtRtnRfdCnt', label: '平台介入退货退款笔数', kind: 'item' },
      { name: 'slrAgrOnlyRfdAmt', label: '卖家同意仅退款金额', kind: 'amount' },
      { name: 'slrAgrOnlyRfdCnt', label: '卖家同意仅退款笔数', kind: 'item' },
      { name: 'slrAgrRtnRfdAmt', label: '卖家同意退货退款金额', kind: 'amount' },
      { name: 'slrAgrRtnRfdCnt', label: '卖家同意退货退款笔数', kind: 'item' },
      { name: 'slrLiabDsptCnt', label: '卖家责任纠纷笔数', kind: 'item' },
      { name: 'slrLiabRate', label: '卖家责任率', kind: 'item' },
    ],
  },
  {
    key: 'success',
    label: '成功退款',
    metrics: [
      { name: 'succRfdAmt', label: '成功退款金额', kind: 'amount' },
      { name: 'succRfdCnt', label: '成功退款笔数', kind: 'item' },
      { name: 'succRfdRate', label: '成功退款率', kind: 'item' },
      { name: 'payOrdCnt', label: '支付笔数', kind: 'item' },
    ],
  },
]

/** 复合卡子指标中文名 */
export const COMPOSITE_LABELS: Record<string, string> = {
  fstByrPayAmt: '首次',
  rptByrPayAmt: '复购',
}

/** 图表可选字段（排除 ds/slrId/timeCycle 元数据） */
export const CHART_METRIC_GROUPS: MetricGroupDef[] = OVERVIEW_GROUPS

/** 格式化 banner 展示值：官方已格式化，'-' 表示无数据 */
export function formatBannerValue(item: BannerDataItem | undefined): string {
  if (!item) return '--'
  if (item.dataStr === '-' || item.dataStr == null || item.dataStr === '') return '--'
  return item.dataStr
}

/** 趋势图 Y 轴数值格式化 */
export function formatChartValue(value: number | string, metric?: MetricDef): string {
  if (typeof value !== 'number') return String(value)
  const fmt = metric?.format ?? 'number'
  if (fmt === 'percent') return `${(value * 100).toFixed(2)}%`
  if (fmt === 'currency') return `¥${value.toFixed(2)}`
  return value >= 1000 ? value.toLocaleString() : String(value)
}
