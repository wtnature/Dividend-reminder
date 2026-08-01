"""
智能轮询调度器（状态机）

状态流转:
  not_announced ──▶ announced_no_date ──▶ payment_locked
       │                      │                  │
  财报季: 每日1次        每日1次          停止轮询
  非财报季: 每周1次                         │
                                         派息日触发提醒

配置 from config/settings.yaml
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from ..db import database as db

logger = logging.getLogger(__name__)


class PollScheduler:
    """轮询状态机调度器"""

    def __init__(self, config: dict):
        """
        Args:
            config: 从 settings.yaml 读取的完整配置
        """
        self.polling_cfg = config.get("polling", {})
        self.earnings_months = self.polling_cfg.get("earnings_season_months", [3, 4, 8, 9])
        self.earnings_interval = self.polling_cfg.get("earnings_season_interval_hours", 24)
        self.off_season_interval = self.polling_cfg.get("off_season_interval_hours", 168)

    # ================================================================
    #  调度决策
    # ================================================================

    def compute_next_check(self, stock_code: str, state: str = None) -> str:
        """
        根据当前月份 + 分红状态，计算下次查询时间。

        返回 ISO 格式时间字符串。
        """
        now = datetime.now()
        is_earnings_season = now.month in self.earnings_months

        if state is None:
            poll_state = db.get_polling_state(stock_code)
            state = poll_state["state"] if poll_state else "not_announced"

        if state == "payment_locked":
            # 已锁定派息日 → 设为很久以后（实际不会触发）
            return (now + timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")

        elif state == "announced_no_date":
            # 已预告但无日期 → 每日 1 次
            interval_hours = 24

        elif state == "not_announced":
            # 未公布 → 财报季每日，非财报季每周
            interval_hours = (self.earnings_interval
                              if is_earnings_season
                              else self.off_season_interval)

        else:
            interval_hours = self.off_season_interval

        next_time = now + timedelta(hours=interval_hours)
        return next_time.strftime("%Y-%m-%d %H:%M:%S")

    # ================================================================
    #  批量更新
    # ================================================================

    def update_all_schedules(self):
        """遍历所有活跃持仓，更新下次查询时间"""
        stocks = db.get_all_stocks(active_only=True)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for stock in stocks:
            stock_code = stock["stock_code"]
            poll_state = db.get_polling_state(stock_code)
            if not poll_state:
                continue

            state = poll_state["state"]
            next_check = self.compute_next_check(stock_code, state=state)

            db.update_polling_state(
                stock_code,
                last_checked_at=now_str,
                next_check_at=next_check,
            )

            logger.debug(f"{stock_code} ({state}): 下次查询 → {next_check}")

    # ================================================================
    #  状态流转
    # ================================================================

    def transition(self, stock_code: str,
                   new_state: str,
                   payment_date: Optional[str] = None):
        """
        手动触发状态流转

        Args:
            stock_code: 股票代码
            new_state: 目标状态 (not_announced / announced_no_date / payment_locked)
            payment_date: 若锁定到 payment_locked，记录派息日
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        next_check = self.compute_next_check(stock_code, state=new_state)

        db.update_polling_state(
            stock_code,
            state=new_state,
            last_checked_at=now_str,
            next_check_at=next_check,
        )

        logger.info(f"{stock_code}: {new_state} (下次检查: {next_check})")

    # ================================================================
    #  检查当前月份是否为财报季
    # ================================================================

    def is_earnings_season(self, month: int = None) -> bool:
        if month is None:
            month = datetime.now().month
        return month in self.earnings_months

    # ================================================================
    #  获取调度摘要
    # ================================================================

    def get_summary(self) -> list[dict]:
        """返回所有股票的轮询状态摘要（供 UI 展示）"""
        return db.get_all_polling_states()
