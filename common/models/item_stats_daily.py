"""
商品运营指标每日快照模型

功能：
1. 存储每件商品每天凌晨采集的运营指标快照（scheduler item_stats_snapshot 任务写入）
2. 支撑商品管理列表展示（读最新快照）与后续优化算法（按日期区间查询历史）

口径说明（实调验证）：
- *_1d 字段：数据罗盘 recent1d 窗口（近24小时滚动，凌晨采集，T+1 口径）
- *_7d 字段：数据罗盘 recent7d 窗口（近7天滚动）
- want_count：闲鱼商品详情接口累计想要数
- days_on_shelf / post_dt：采集时刻的当前状态值（非窗口数据）
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base, TimestampMixin


class ItemStatsDaily(TimestampMixin, Base):
    """商品运营指标每日快照表"""

    __tablename__ = "xy_item_stats_daily"
    __table_args__ = (
        Index("uk_item_stats_acc_item_date", "account_id", "item_id", "stat_date", unique=True),
        Index("idx_item_stats_stat_date", "stat_date"),
        Index("idx_item_stats_item", "item_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    account_id: Mapped[str] = mapped_column(String(80), nullable=False, comment="账号标识")
    item_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="闲鱼商品ID")
    stat_date: Mapped[str] = mapped_column(String(8), nullable=False, comment="快照日期 yyyyMMdd（采集日期）")

    # 近24小时窗口指标（recent1d）
    show_pv_1d: Mapped[int | None] = mapped_column(Integer, comment="当日曝光次数")
    show_uv_1d: Mapped[int | None] = mapped_column(Integer, comment="当日曝光人数")
    ipv_1d: Mapped[int | None] = mapped_column(Integer, comment="当日浏览次数")
    ipv_uv_1d: Mapped[int | None] = mapped_column(Integer, comment="当日浏览人数")
    chat_uv_1d: Mapped[int | None] = mapped_column(Integer, comment="当日咨询人数")
    pay_ord_cnt_1d: Mapped[int | None] = mapped_column(Integer, comment="当日支付笔数")
    pay_byr_cnt_1d: Mapped[int | None] = mapped_column(Integer, comment="当日支付买家数")
    pay_amt_1d: Mapped[str | None] = mapped_column(String(32), comment="当日支付金额")
    ipv_pay_ucvr_1d: Mapped[str | None] = mapped_column(String(16), comment="当日浏览支付转化率")

    # 近7天窗口指标（recent7d）
    show_pv_7d: Mapped[int | None] = mapped_column(Integer, comment="近7天曝光次数")
    show_uv_7d: Mapped[int | None] = mapped_column(Integer, comment="近7天曝光人数")
    ipv_7d: Mapped[int | None] = mapped_column(Integer, comment="近7天浏览次数")
    ipv_uv_7d: Mapped[int | None] = mapped_column(Integer, comment="近7天浏览人数")
    chat_uv_7d: Mapped[int | None] = mapped_column(Integer, comment="近7天咨询人数")
    pay_ord_cnt_7d: Mapped[int | None] = mapped_column(Integer, comment="近7天支付笔数")
    pay_byr_cnt_7d: Mapped[int | None] = mapped_column(Integer, comment="近7天支付买家数")
    pay_amt_7d: Mapped[str | None] = mapped_column(String(32), comment="近7天支付金额")
    ipv_pay_ucvr_7d: Mapped[str | None] = mapped_column(String(16), comment="近7天浏览支付转化率")

    # 累计值 / 状态值
    want_count: Mapped[int | None] = mapped_column(Integer, comment="累计想要数（商品详情接口）")
    days_on_shelf: Mapped[int | None] = mapped_column(Integer, comment="上架天数（采集时刻）")
    post_dt: Mapped[str | None] = mapped_column(String(8), comment="上架日期 yyyyMMdd")
