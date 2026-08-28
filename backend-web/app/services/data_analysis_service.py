"""
数据分析服务

功能：
1. 调用闲鱼卖家数据罗盘API获取各类数据
2. 支持多账号查询
3. 支持多种时间范围（近1天、近7天、近30天、自定义）
4. 带重试机制

已接入接口（均经真实账号实调验证）：
- seller.summary   卖家数据概览（36 指标 + 趋势图）
- browse.summary   流量分布（来源/商品/时间/地域）
- item.summary     商品维度概览（14 指标）
- repurchase.summary 复购概览（10 指标）
- refund.summary   退款分析（34 指标）
- fans.summary     粉丝概况
- cs.overview.summary 客服概览
- item.list        商品列表（分页）
- item.indicators  单品指标（流量/交易/综合三组）
- flow.detail      流量转化漏斗
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict

import aiohttp
from loguru import logger

from common.utils.xianyu_utils import generate_sign, trans_cookies


# 数据罗盘接口清单
SELLER_SUMMARY_API = "mtop.alibaba.idle.seller.pc.datacompass.singleuser.seller.summary"
BROWSE_SUMMARY_API = "mtop.alibaba.idle.seller.pc.datacompass.singleuser.browse.summary"
ITEM_SUMMARY_API = "mtop.alibaba.idle.seller.pc.datacompass.singleuser.item.summary"
REPURCHASE_SUMMARY_API = "mtop.alibaba.idle.seller.pc.datacompass.singleuser.repurchase.summary"
REFUND_SUMMARY_API = "mtop.alibaba.idle.seller.pc.datacompass.refund.summary"
FANS_SUMMARY_API = "mtop.alibaba.idle.seller.pc.datacompass.fans.summary"
CS_OVERVIEW_SUMMARY_API = "mtop.alibaba.idle.seller.pc.datacompass.cs.overview.summary"
ITEM_LIST_API = "mtop.alibaba.idle.seller.pc.datacompass.item.list"
ITEM_INDICATORS_API = "mtop.alibaba.idle.seller.pc.datacompass.item.indicators"
FLOW_DETAIL_API = "mtop.alibaba.idle.seller.pc.datacompass.flow.detail"

# 最大重试次数
MAX_RETRY = 3
# 重试间隔（秒）
RETRY_DELAY = 1.0


async def _call_datacompass(
    api: str,
    cookies_str: str,
    data_obj: Dict[str, Any],
    retry_count: int = 0,
) -> Dict[str, Any]:
    """
    调用闲鱼数据罗盘接口的公共实现

    Args:
        api: mtop 接口名
        cookies_str: 账号Cookie字符串
        data_obj: 请求体（data 字段）
        retry_count: 当前重试次数

    Returns:
        {"success": bool, "message": str, "data": 接口原始 data 字段}
    """
    if retry_count >= MAX_RETRY:
        logger.error(f"数据罗盘接口 {api} 失败，已达最大重试次数({MAX_RETRY})")
        return {"success": False, "message": f"请求失败，已重试{MAX_RETRY}次"}

    if not cookies_str:
        return {"success": False, "message": "账号Cookie为空"}

    try:
        cookies = trans_cookies(cookies_str)
    except Exception as e:
        return {"success": False, "message": f"Cookie解析失败: {e}"}

    # 生成时间戳和签名
    timestamp = str(int(time.time() * 1000))
    data_val = json.dumps(data_obj, separators=(",", ":"), ensure_ascii=False)
    token = cookies.get("_m_h5_tk", "").split("_")[0] if cookies.get("_m_h5_tk") else ""
    sign = generate_sign(timestamp, token, data_val)

    # 构建请求参数
    params = {
        "jsv": "2.7.2",
        "appKey": "34839810",
        "t": timestamp,
        "sign": sign,
        "v": "1.0",
        "type": "originaljson",
        "accountSite": "xianyu",
        "dataType": "json",
        "timeout": "20000",
        "showErrorToast": "true",
        "api": api,
        "sessionOption": "AutoLoginOnly",
    }

    # 构建请求头
    headers = {
        "accept": "application/json",
        "content-type": "application/x-www-form-urlencoded",
        "cookie": cookies_str,
        "Referer": "https://seller.goofish.com/?site=COMMONPRO",
        "idle_site_biz_code": "COMMONPRO",
        "idle_user_group_member_id": "",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
    }

    url = f"https://h5api.m.goofish.com/h5/{api}/1.0/"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                params=params,
                data={"data": data_val},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                try:
                    res_json = await response.json(content_type=None)
                except Exception:
                    text = await response.text()
                    logger.warning(f"{api} 响应解析失败: {text[:200]}")
                    await asyncio.sleep(RETRY_DELAY * (retry_count + 1))
                    return await _call_datacompass(api, cookies_str, data_obj, retry_count + 1)

                ret = res_json.get("ret", [])
                ret_str = ret[0] if ret else ""

                if "SUCCESS" in ret_str:
                    return {
                        "success": True,
                        "message": "获取成功",
                        "data": res_json.get("data", {}),
                    }
                else:
                    logger.warning(f"{api} 返回错误: {ret_str}")
                    await asyncio.sleep(RETRY_DELAY * (retry_count + 1))
                    return await _call_datacompass(api, cookies_str, data_obj, retry_count + 1)

    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning(f"{api} 请求失败(第{retry_count + 1}次): {e}")
        await asyncio.sleep(RETRY_DELAY * (retry_count + 1))
        return await _call_datacompass(api, cookies_str, data_obj, retry_count + 1)
    except Exception as e:
        logger.error(f"{api} 请求异常: {e}")
        return {"success": False, "message": f"请求异常: {str(e)}"}


def _build_date_payload(date_type: str, date_range: str) -> Dict[str, str]:
    """构建时间范围请求体（customDate 时附带 dateRange）"""
    data_obj: Dict[str, str] = {"dateType": date_type}
    if date_type == "customDate" and date_range:
        data_obj["dateRange"] = date_range
    return data_obj


async def fetch_seller_summary(
    cookies_str: str,
    date_type: str = "recent7d",
    date_range: str = "",
    retry_count: int = 0,
) -> Dict[str, Any]:
    """
    获取卖家数据概览

    Args:
        cookies_str: 账号Cookie字符串
        date_type: 时间范围类型（recent1d/recent7d/recent30d/customDate）
        date_range: 自定义日期范围（可选，格式: yyyyMMdd|yyyyMMdd）
        retry_count: 当前重试次数

    Returns:
        API返回的数据字典
    """
    # 卖家概览接口的固定请求体（与官方页面一致）
    data_obj = {
        "dateRange": date_range,
        "dateType": date_type,
        "ms": "",
        "selectedSellerId": "undefined",
    }
    return await _call_datacompass(SELLER_SUMMARY_API, cookies_str, data_obj, retry_count)


async def fetch_browse_summary(
    cookies_str: str,
    date_type: str = "recent7d",
    date_range: str = "",
    retry_count: int = 0,
) -> Dict[str, Any]:
    """
    获取流量分布数据（来源分布、商品分布、时间分布、地域分布）

    Args:
        cookies_str: 账号Cookie字符串
        date_type: 时间范围类型（recent1d/recent7d/recent30d/customDate）
        date_range: 自定义日期范围（可选，格式: yyyyMMdd|yyyyMMdd）
        retry_count: 当前重试次数

    Returns:
        API返回的数据字典
    """
    data_obj = _build_date_payload(date_type, date_range)
    return await _call_datacompass(BROWSE_SUMMARY_API, cookies_str, data_obj, retry_count)


async def fetch_item_summary(
    cookies_str: str,
    date_type: str = "recent7d",
    date_range: str = "",
) -> Dict[str, Any]:
    """
    获取商品维度概览（14 个商品运营指标）

    Args:
        cookies_str: 账号Cookie字符串
        date_type: 时间范围类型（recent1d/recent7d/recent30d/customDate）
        date_range: 自定义日期范围（可选，格式: yyyyMMdd|yyyyMMdd）

    Returns:
        API返回的数据字典
    """
    data_obj = _build_date_payload(date_type, date_range)
    return await _call_datacompass(ITEM_SUMMARY_API, cookies_str, data_obj)


async def fetch_repurchase_summary(
    cookies_str: str,
    date_type: str = "recent7d",
    date_range: str = "",
) -> Dict[str, Any]:
    """
    获取复购概览（复购订单数、复购买家数、复购率等 10 个指标）

    Args:
        cookies_str: 账号Cookie字符串
        date_type: 时间范围类型（recent1d/recent7d/recent30d/customDate）
        date_range: 自定义日期范围（可选，格式: yyyyMMdd|yyyyMMdd）

    Returns:
        API返回的数据字典
    """
    data_obj = _build_date_payload(date_type, date_range)
    return await _call_datacompass(REPURCHASE_SUMMARY_API, cookies_str, data_obj)


async def fetch_refund_summary(
    cookies_str: str,
    date_type: str = "recent7d",
    date_range: str = "",
) -> Dict[str, Any]:
    """
    获取退款分析（发货前/后、仅退款/退货退款、平台介入等 34 个指标）

    Args:
        cookies_str: 账号Cookie字符串
        date_type: 时间范围类型（recent1d/recent7d/recent30d/customDate）
        date_range: 自定义日期范围（可选，格式: yyyyMMdd|yyyyMMdd）

    Returns:
        API返回的数据字典
    """
    data_obj = _build_date_payload(date_type, date_range)
    return await _call_datacompass(REFUND_SUMMARY_API, cookies_str, data_obj)


async def fetch_fans_summary(
    cookies_str: str,
    date_type: str = "recent7d",
    date_range: str = "",
) -> Dict[str, Any]:
    """
    获取粉丝概况（粉丝总数、新增粉丝、粉丝下单占比 + 趋势图）

    Args:
        cookies_str: 账号Cookie字符串
        date_type: 时间范围类型（recent1d/recent7d/recent30d/customDate）
        date_range: 自定义日期范围（可选，格式: yyyyMMdd|yyyyMMdd）

    Returns:
        API返回的数据字典
    """
    data_obj = _build_date_payload(date_type, date_range)
    return await _call_datacompass(FANS_SUMMARY_API, cookies_str, data_obj)


async def fetch_cs_overview_summary(
    cookies_str: str,
    date_type: str = "recent7d",
    date_range: str = "",
) -> Dict[str, Any]:
    """
    获取客服概览（响应时长、回复率、满意度、客服成交等 14 个指标 + 趋势图）

    Args:
        cookies_str: 账号Cookie字符串
        date_type: 时间范围类型（recent1d/recent7d/recent30d/customDate）
        date_range: 自定义日期范围（可选，格式: yyyyMMdd|yyyyMMdd）

    Returns:
        API返回的数据字典
    """
    data_obj = _build_date_payload(date_type, date_range)
    return await _call_datacompass(CS_OVERVIEW_SUMMARY_API, cookies_str, data_obj)


async def fetch_item_list(
    cookies_str: str,
    date_type: str = "recent7d",
    date_range: str = "",
    page_num: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """
    获取商品列表（分页，含单品曝光/浏览/咨询/成交/退款数据）

    Args:
        cookies_str: 账号Cookie字符串
        date_type: 时间范围类型（recent1d/recent7d/recent30d/customDate）
        date_range: 自定义日期范围（可选，格式: yyyyMMdd|yyyyMMdd）
        page_num: 页码（从1开始）
        page_size: 每页条数

    Returns:
        API返回的数据字典
    """
    data_obj = {
        **_build_date_payload(date_type, date_range),
        "pageNum": page_num,
        "pageSize": page_size,
    }
    return await _call_datacompass(ITEM_LIST_API, cookies_str, data_obj)


async def fetch_item_indicators(
    cookies_str: str,
    item_id: str,
    date_type: str = "recent7d",
    date_range: str = "",
) -> Dict[str, Any]:
    """
    获取单品指标字段定义表（流量指标 5 项 + 交易指标 4 项 + 综合指标 6 项）

    注意：官方接口仅返回字段 label 表，数值取自 item.list 的行数据。
    item_id 传空时同样返回定义表。

    Args:
        cookies_str: 账号Cookie字符串
        item_id: 闲鱼商品ID（可为空）
        date_type: 时间范围类型（recent1d/recent7d/recent30d/customDate）
        date_range: 自定义日期范围（可选，格式: yyyyMMdd|yyyyMMdd）

    Returns:
        API返回的数据字典
    """
    data_obj = {
        **_build_date_payload(date_type, date_range),
        "itemId": item_id,
    }
    return await _call_datacompass(ITEM_INDICATORS_API, cookies_str, data_obj)


async def fetch_flow_detail(
    cookies_str: str,
    date_type: str = "recent7d",
    date_range: str = "",
) -> Dict[str, Any]:
    """
    获取流量转化漏斗（曝光→浏览→咨询→支付各环节 UV 与转化率）

    Args:
        cookies_str: 账号Cookie字符串
        date_type: 时间范围类型（recent1d/recent7d/recent30d/customDate）
        date_range: 自定义日期范围（可选，格式: yyyyMMdd|yyyyMMdd）

    Returns:
        API返回的数据字典
    """
    data_obj = _build_date_payload(date_type, date_range)
    return await _call_datacompass(FLOW_DETAIL_API, cookies_str, data_obj)
