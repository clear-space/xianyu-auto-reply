"""
定时发布规则时间计算工具

功能：
1. 计算定时发布规则的下一次触发时间（next_trigger_at）
2. 支持单次（once）/ 每天（daily）/ 每周（weekly）三种重复模式
3. 支持固定时间点列表 与 时间段内随机 两种时间配置

验收要求：时间计算函数只维护一份 —— backend-web 与 scheduler 均从本模块导入，
禁止在各自服务中复制实现。
"""
from __future__ import annotations

import random
from datetime import datetime, time, timedelta
from typing import Optional

from common.utils.time_utils import BEIJING_TZ, get_beijing_now


def parse_time(t_str: str) -> Optional[time]:
    """解析 HH:MM 格式的时间字符串"""
    try:
        h, m = t_str.strip().split(":")
        return time(int(h), int(m))
    except (ValueError, TypeError):
        return None


def random_time_between(start_str: str, end_str: str) -> time:
    """在时间段内随机生成一个时间点（精确到分钟）"""
    start = parse_time(start_str) or time(0, 0)
    end = parse_time(end_str) or time(23, 59)
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    if end_minutes <= start_minutes:
        end_minutes = start_minutes + 1  # 至少间隔1分钟
    random_minutes = random.randint(start_minutes, end_minutes)
    return time(random_minutes // 60, random_minutes % 60)


def _sorted_time_points(times: list) -> list[time]:
    points = [parse_time(t) for t in times if parse_time(t) is not None]
    points.sort()
    return points


def _first_time_point(config: dict) -> time:
    """取配置里的最早时间点，无有效时间点则默认 00:00"""
    points = _sorted_time_points(config.get("times", []))
    return points[0] if points else time(0, 0)


def _pick_trigger_time(config: dict) -> time:
    """按配置取一个时间点：时间段随机 或 固定时间点"""
    use_random = config.get("random", False)
    time_range = config.get("time_range")
    if use_random and time_range:
        return random_time_between(
            time_range.get("start", "00:00"),
            time_range.get("end", "23:59"),
        )
    return _first_time_point(config)


def _next_daily(config: dict, now: datetime, today) -> Optional[datetime]:
    """计算每天的 next_trigger_at"""
    use_random = config.get("random", False)
    time_range = config.get("time_range")
    times = config.get("times", [])

    if use_random and time_range:
        # 时间段随机：每次计算都随机一个时间点
        candidate = datetime.combine(today, _pick_trigger_time(config)).replace(tzinfo=BEIJING_TZ)
        if candidate <= now:
            candidate = datetime.combine(
                today + timedelta(days=1), _pick_trigger_time(config)
            ).replace(tzinfo=BEIJING_TZ)
        return candidate

    if times:
        # 指定时间点列表：找到今天第一个未过的时间
        time_points = _sorted_time_points(times)
        for tp in time_points:
            candidate = datetime.combine(today, tp).replace(tzinfo=BEIJING_TZ)
            if candidate > now:
                return candidate
        # 今天都过了，取明天第一个时间点
        if time_points:
            return datetime.combine(today + timedelta(days=1), time_points[0]).replace(tzinfo=BEIJING_TZ)

    # 默认 00:00
    candidate = datetime.combine(today, time(0, 0)).replace(tzinfo=BEIJING_TZ)
    if candidate <= now:
        candidate = datetime.combine(today + timedelta(days=1), time(0, 0)).replace(tzinfo=BEIJING_TZ)
    return candidate


def _next_weekly(config: dict, now: datetime, today) -> Optional[datetime]:
    """计算每周的 next_trigger_at"""
    days = config.get("days", [])
    if not days:
        return None

    use_random = config.get("random", False)
    time_range = config.get("time_range")
    times = config.get("times", [])

    for offset in range(8):  # 最多查一周
        candidate_date = today + timedelta(days=offset)
        candidate_weekday = candidate_date.isoweekday()

        if candidate_weekday not in days:
            continue

        # 如果就是今天，检查时间是否已过
        if offset == 0:
            if use_random and time_range:
                trigger_time = _pick_trigger_time(config)
                candidate = datetime.combine(candidate_date, trigger_time).replace(tzinfo=BEIJING_TZ)
                if candidate > now:
                    return candidate
                continue  # 今天已过，找下一个
            if times:
                time_points = _sorted_time_points(times)
                for tp in time_points:
                    candidate = datetime.combine(candidate_date, tp).replace(tzinfo=BEIJING_TZ)
                    if candidate > now:
                        return candidate
                continue  # 今天已过，找下一个
            # 默认用 00:00
            candidate = datetime.combine(candidate_date, time(0, 0)).replace(tzinfo=BEIJING_TZ)
            if candidate > now:
                return candidate
            continue

        # 未来某天，直接用最早时间点
        return datetime.combine(candidate_date, _pick_trigger_time(config)).replace(tzinfo=BEIJING_TZ)

    return None


def compute_next_trigger(
    schedule_mode: str, schedule_config: dict, after: datetime = None
) -> Optional[datetime]:
    """
    根据调度配置计算下一次触发时间。

    schedule_config 结构：
      - once:     {"datetime": "2026-08-01T20:00:00"}
      - daily:    {"times": ["08:00", "20:00"]}  或  {"time_range": {"start":"18:00","end":"22:00"}, "random": true}
      - weekly:   {"days": [1,3,5], "times": ["20:00"]}  或  {"days":[1,3,5], "time_range": {...}, "random": true}

    Args:
        schedule_mode: once / daily / weekly
        schedule_config: 时间配置 JSON
        after: 计算此时间之后的下一次触发，默认当前时间

    Returns:
        下次触发的 datetime（北京时间），如无法计算（如 once 已过期）返回 None
    """
    now = after if after else get_beijing_now()
    today = now.date()

    if schedule_mode == "once":
        dt_str = schedule_config.get("datetime")
        if not dt_str:
            return None
        try:
            dt = datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            return None
        # 确保时区一致：如果解析的是 naive datetime，补上北京时间
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BEIJING_TZ)
        # 如果时间已过，返回 None（调用方应禁用规则）
        if dt <= now:
            return None
        return dt

    if schedule_mode == "daily":
        return _next_daily(schedule_config, now, today)

    if schedule_mode == "weekly":
        return _next_weekly(schedule_config, now, today)

    return None


__all__ = [
    "compute_next_trigger",
    "parse_time",
    "random_time_between",
]
