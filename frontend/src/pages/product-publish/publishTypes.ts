/**
 * 单品发布页面共享数据结构。
 * 规格数据只用于本次发布，不写入素材库。
 */
import type { MaterialVideo, PlatformCategoryCandidate, PlatformCategoryPathItem, PlatformMaterialAttribute } from '@/api/productPublish'

export type ShippingMethod = 'free' | 'distance' | 'fixed' | 'template' | 'none'

/** 发货方式选项，批量导入与新建素材共用。 */
export const SHIPPING_OPTIONS: Array<{ value: ShippingMethod; label: string }> = [
  { value: 'free', label: '包邮' },
  { value: 'distance', label: '按距离计费' },
  { value: 'fixed', label: '一口价' },
  { value: 'template', label: '运费模板' },
  { value: 'none', label: '无需邮寄' },
]

export interface SpecificationValue {
  id: string
  name: string
  image?: string | null
}

export interface ProductSpecification {
  id: string
  name: string
  values: SpecificationValue[]
  supportImage: boolean
}

export interface SkuRow {
  key: string
  specs: Record<string, string>
  price: string
  stock: string
}

export interface DuplicateSpecificationValue {
  specificationName: string
  valueName: string
}

/** 查找同一规格类型下重复的规格值，供素材保存和单品发布共同校验。 */
export function findDuplicateSpecificationValue(
  specifications: ProductSpecification[],
): DuplicateSpecificationValue | null {
  for (const specification of specifications) {
    const values = new Set<string>()
    for (const value of specification.values) {
      const valueName = value.name.trim()
      if (!valueName) continue
      if (values.has(valueName)) {
        return {
          specificationName: specification.name.trim() || '未命名规格',
          valueName,
        }
      }
      values.add(valueName)
    }
  }
  return null
}

/** 按规格定义顺序生成稳定的 SKU key，确保素材库导入后能匹配原价格和库存。 */
export function buildSkuKey(specifications: ProductSpecification[], specs: Record<string, string>): string {
  return specifications
    .filter((specification) => specification.name.trim() && specification.values.some((value) => value.name.trim()))
    .map((specification) => `${specification.name}:${specs[specification.name] || ''}`)
    .join('|')
}

export interface PublishForm {
  account_id: string
  title: string
  description: string
  price: string
  original_price: string
  category: string
  platform_category_id: string
  platform_category_name: string
  platform_channel_category_id: string
  platform_channel_category_name: string
  platform_leaf_id: string
  platform_tb_category_id: string
  platform_category_path: PlatformCategoryPathItem[]
  platform_attributes: PlatformMaterialAttribute[]
  category_source: 'manual' | 'recommendation'
  category_confidence?: number
  videos: MaterialVideo[]
  quantity: number
  address: string
  address_expected_text?: string
  delivery_method: 'express' | 'pickup'
  shipping_method: ShippingMethod
  support_pickup: boolean
  postage: string
  brand: string
  condition: string
  specifications: ProductSpecification[]
  sku_rows: SkuRow[]
}

/**
 * 内置兜底分类（ID 来自闲鱼分类推荐接口实测返回）。
 * 未手动选择分类时默认使用第一项「电子资料」，发布被平台拒绝时后端会自动回退下一项。
 */
export const DEFAULT_PLATFORM_CATEGORIES: PlatformCategoryCandidate[] = [
  {
    cat_id: '50023914',
    cat_name: '电子资料',
    channel_cat_id: '202036301',
    channel_cat_name: '电子资料',
    leaf_id: null,
    tb_cat_id: null,
    path: [{ id: '50023914', name: '电子资料' }] as PlatformCategoryPathItem[],
    score: null,
  },
  {
    cat_id: null,
    cat_name: '其他闲置',
    channel_cat_id: '201459411',
    channel_cat_name: '其他闲置',
    leaf_id: null,
    tb_cat_id: null,
    path: [{ id: '201459411', name: '其他闲置' }] as PlatformCategoryPathItem[],
    score: null,
  },
]

/** 内置默认分类写入表单的补丁（含分类路径与来源标记）。 */
export function defaultCategoryFormPatch(): Pick<PublishForm,
  | 'category'
  | 'platform_category_id'
  | 'platform_category_name'
  | 'platform_channel_category_id'
  | 'platform_channel_category_name'
  | 'platform_leaf_id'
  | 'platform_tb_category_id'
  | 'platform_category_path'
  | 'platform_attributes'
  | 'category_source'
  | 'category_confidence'
> {
  const fallback = DEFAULT_PLATFORM_CATEGORIES[0]
  return {
    category: fallback.cat_name || '',
    platform_category_id: fallback.cat_id || '',
    platform_category_name: fallback.cat_name || '',
    platform_channel_category_id: fallback.channel_cat_id || '',
    platform_channel_category_name: fallback.channel_cat_name || '',
    platform_leaf_id: fallback.leaf_id || '',
    platform_tb_category_id: fallback.tb_cat_id || '',
    platform_category_path: fallback.path,
    platform_attributes: [],
    category_source: 'manual',
    category_confidence: undefined,
  }
}
