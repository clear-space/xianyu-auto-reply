/**
 * 数据分析API
 *
 * 提供卖家数据概览接口调用
 */
import { post } from '@/utils/request'

const DATA_ANALYSIS_PREFIX = '/api/v1/data-analysis'

/** 卖家数据概览请求参数 */
export interface SellerSummaryRequest {
  account_id: number
  date_type: 'recent1d' | 'recent7d' | 'recent30d' | 'customDate'
  date_range?: string
}

/** Banner数据项 */
export interface BannerDataItem {
  name: string
  cycle: string
  data: number
  dataFormat: string
  dataStr: string
  decimal: boolean
  lastData?: number
  lastDataFormat?: string
  lastDataStr?: string
  ratio?: number
  ratioFormat?: string
  extendInfo?: Record<string, string>
}

/** 图表数据项（每日数据） */
export interface GraphDataItem {
  ds: string
  payAmt: number
  payOrdCnt: number
  payByrCnt: number
  showPv: number
  showUv: number
  ipv: number
  ipvUv: number
  vstPv: number
  vstUv: number
  chatUv: number
  aov: number
  rfdAmt: number
  rfdOrdCnt: number
  showItmCnt: number
  ipvItmCnt: number
  stItmCnt: number
  uctr: number
  onlCnt: number
  rptOrdCnt: number
  rptByrCnt: number
  rpr: number
  fstByrPayAmt: number
  rptByrPayAmt: number
  showPvCmpPctl: number
  payOrdCntCmpPctl: number
  rep3minUvRate: number
  [key: string]: number | string
}

/** 卖家数据概览响应数据 */
export interface SellerSummaryData {
  code: string
  data: {
    graphBannerBenchData: {
      bannerDataList: BannerDataItem[]
      graphDataList: GraphDataItem[]
    }
  }
  extendInfo?: {
    realDateRange?: string[]
  }
  msg: string
}

/** API响应格式 */
export interface SellerSummaryResponse {
  success: boolean
  message: string | null
  data: SellerSummaryData | null
}

/**
 * 获取卖家数据概览
 */
export const getSellerSummary = async (
  payload: SellerSummaryRequest,
): Promise<SellerSummaryResponse> => {
  return post<SellerSummaryResponse>(`${DATA_ANALYSIS_PREFIX}/seller-summary`, payload)
}


/** 流量分布请求参数 */
export interface BrowseSummaryRequest {
  account_id: number
  date_type: 'recent1d' | 'recent7d' | 'recent30d' | 'customDate'
  date_range?: string
}

/** 分布数据项 */
export interface ProfileItem {
  profileCode: string
  profileVal: string
  usrRatio: number
  usrRatioFormat: string
}

/** 流量分布响应数据 */
export interface BrowseSummaryData {
  sceneSourceList: ProfileItem[]
  itemCateList: ProfileItem[]
  buyerActiveList: ProfileItem[]
  buyerProvinceList: ProfileItem[]
}

/** 流量分布API响应 */
export interface BrowseSummaryResponse {
  success: boolean
  message: string | null
  data: { code: string; data: BrowseSummaryData; msg: string } | null
}

/**
 * 获取流量分布数据
 */
export const getBrowseSummary = async (
  payload: BrowseSummaryRequest,
): Promise<BrowseSummaryResponse> => {
  return post<BrowseSummaryResponse>(`${DATA_ANALYSIS_PREFIX}/browse-summary`, payload)
}

/* ============ 新增模块（经官方接口实调验证） ============ */

/** 概览型接口（item/repurchase/fans/cs-overview）响应：与卖家概览同构 */
export interface OverviewModuleResponse {
  success: boolean
  message: string | null
  data: {
    code: string
    data: {
      graphBannerBenchData: {
        bannerDataList: BannerDataItem[]
        graphDataList?: GraphDataItem[]
      }
    }
    extendInfo?: { realDateRange?: string[] }
    msg: string
  } | null
}

/** 商品维度概览（14 个商品运营指标） */
export const getItemSummary = async (
  payload: SellerSummaryRequest,
): Promise<OverviewModuleResponse> => {
  return post<OverviewModuleResponse>(`${DATA_ANALYSIS_PREFIX}/item-summary`, payload)
}

/** 复购概览（10 个复购指标） */
export const getRepurchaseSummary = async (
  payload: SellerSummaryRequest,
): Promise<OverviewModuleResponse> => {
  return post<OverviewModuleResponse>(`${DATA_ANALYSIS_PREFIX}/repurchase-summary`, payload)
}

/** 粉丝概况（3 banner + 趋势） */
export const getFansSummary = async (
  payload: SellerSummaryRequest,
): Promise<OverviewModuleResponse> => {
  return post<OverviewModuleResponse>(`${DATA_ANALYSIS_PREFIX}/fans-summary`, payload)
}

/** 客服概览（14 指标 + 趋势） */
export const getCsOverviewSummary = async (
  payload: SellerSummaryRequest,
): Promise<OverviewModuleResponse> => {
  return post<OverviewModuleResponse>(`${DATA_ANALYSIS_PREFIX}/cs-overview-summary`, payload)
}

/** 退款分析响应：金额为裸数值，笔数/比率为 BannerDataItem 结构 */
export type RefundMetricValue = number | BannerDataItem

export interface RefundSummaryResponse {
  success: boolean
  message: string | null
  data: {
    code: string
    data: Record<string, RefundMetricValue>
    extendInfo?: { realDateRange?: string[] }
    msg: string
  } | null
}

/** 退款分析（34 个指标） */
export const getRefundSummary = async (
  payload: SellerSummaryRequest,
): Promise<RefundSummaryResponse> => {
  return post<RefundSummaryResponse>(`${DATA_ANALYSIS_PREFIX}/refund-summary`, payload)
}

/** 商品列表行 */
export interface ItemListRow {
  itmId: string
  itmName: string
  itmPicUrl: string
  itmPrice: string
  daysOnShelf: number
  postDt: string
  showPv: number
  showUv: number
  ipv: number
  ipvUv: number
  chatUv: number
  payAmt: string
  payOrdCnt: number
  payByrCnt: number
  ipvPayUcvr: string
  crtRfdAmt: string
  crtRfdByrCnt: number
  crtRfdOrdCnt: number
  succRfdAmt: string
  succRfdByrCnt: number
  succRfdOrdCnt: number
  chnlCateLevel1Name?: string
  chnlCateLevel2Name?: string
  chnlCateLevel3Name?: string
  chnlCateIndustryName?: string
  [key: string]: unknown
}

export interface ItemListRequest extends SellerSummaryRequest {
  page_num?: number
  page_size?: number
}

export interface ItemListResponse {
  success: boolean
  message: string | null
  data: {
    code: string
    data: {
      list: ItemListRow[]
      pageNo: number
      pageSize: number
      rowLimit: number
      rowOffset: number
      total: number
    }
    extendInfo?: { allItemCnt?: number; new3DaysAllItemCnt?: number; realDateRange?: string[] }
    msg: string
  } | null
}

/** 商品列表（分页） */
export const getItemList = async (payload: ItemListRequest): Promise<ItemListResponse> => {
  return post<ItemListResponse>(`${DATA_ANALYSIS_PREFIX}/item-list`, payload)
}

/** 单品指标字段定义（官方返回的是 label 表，数值取自商品列表行） */
export interface IndicatorFieldDef {
  name: string
  title: string
}

export interface IndicatorGroupDef {
  name: string
  title: string
  subFields: IndicatorFieldDef[]
}

export interface ItemIndicatorsResponse {
  success: boolean
  message: string | null
  data: {
    code: string
    data: IndicatorGroupDef[]
    msg: string
  } | null
}

/** 单品指标 schema（label 表） */
export const getItemIndicators = async (
  payload: { account_id: number; item_id: string; date_type?: string; date_range?: string },
): Promise<ItemIndicatorsResponse> => {
  return post<ItemIndicatorsResponse>(`${DATA_ANALYSIS_PREFIX}/item-indicators`, payload)
}

/** 流量转化漏斗数据
 *  注意（实调验证）：itemFlowTransferData 中每个指标都是 BannerDataItem 结构，
 *  另有 ds/scene/timeCycle 三个标量字段。展示统一用 dataStr（官方已格式化）。 */
export interface FlowTransferData {
  ds?: string
  scene?: string
  timeCycle?: string
  showPv?: BannerDataItem
  showUv?: BannerDataItem
  showItmCnt?: BannerDataItem
  ipv?: BannerDataItem
  ipvUv?: BannerDataItem
  ipvItmCnt?: BannerDataItem
  chatUv?: BannerDataItem
  chatCnt?: BannerDataItem
  ipvChatUcvr?: BannerDataItem
  chatPayUcvr?: BannerDataItem
  ipvPayUcvr?: BannerDataItem
  payUv?: BannerDataItem
  payAmt?: BannerDataItem
  rep3minUvRate?: BannerDataItem
  uctr?: BannerDataItem
  uctcvr?: BannerDataItem
  stItmCnt?: BannerDataItem
  [key: string]: unknown
}

export interface FlowDetailResponse {
  success: boolean
  message: string | null
  data: {
    code: string
    data: { itemFlowTransferData: FlowTransferData }
    extendInfo?: { realDateRange?: string[] }
    msg: string
  } | null
}

/** 流量转化漏斗 */
export const getFlowDetail = async (
  payload: SellerSummaryRequest,
): Promise<FlowDetailResponse> => {
  return post<FlowDetailResponse>(`${DATA_ANALYSIS_PREFIX}/flow-detail`, payload)
}
