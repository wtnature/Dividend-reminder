"""
分红数据引擎 — 双源对比仲裁

核心流程:
  1. 同时从 AkShare + 新浪获取数据
  2. 交叉验证派息日期
  3. 日期一致 → 自动处理，计算净到账
  4. 日期不一致 → 标记 date_conflict，通知用户介入
  5. 仅金额不一致 → 随机选一个，不阻塞
  6. 查不到日期 → 标记 needs_review，阻塞提醒
  7. 查不到金额 → 标记 amount_missing，继续运行
"""

import logging
import random
from datetime import datetime
from typing import Optional

from ..data_sources.base_fetcher import DividendData
from ..data_sources.akshare_fetcher import AkShareFetcher
from ..data_sources.sina_fetcher import SinaFetcher
from ..db import database as db
from .tax_calculator import calculate_net, TaxResult

logger = logging.getLogger(__name__)


class DividendEngine:
    """分红数据获取 + 对比 + 仲裁 + 存储"""

    def __init__(self):
        self.fetcher_a = AkShareFetcher()   # 主源
        self.fetcher_b = SinaFetcher()      # 备用源

    # ================================================================
    #  单只股票处理入口
    # ================================================================

    def process_stock(self, stock_code: str) -> dict:
        """
        处理单只股票：抓取 → 对比 → 仲裁 → 计算 → 存储
        返回状态摘要 dict
        """
        stock = db.get_stock(stock_code)
        if not stock:
            return {"stock_code": stock_code, "status": "error",
                    "message": "持仓中不存在该股票"}

        # 1. 双源获取
        data_a = self.fetcher_a.fetch_dividend(stock_code)
        data_b = self.fetcher_b.fetch_dividend(stock_code)

        # 2. 仲裁
        arbitration = self._arbitrate(data_a, data_b)

        # 3. 存储结果
        self._save_result(stock, arbitration)

        # 4. 更新轮询状态
        self._update_polling_after_fetch(stock_code, arbitration)

        return {
            "stock_code": stock_code,
            "stock_name": stock["stock_name"],
            "status": arbitration["status"],
            "payment_date": arbitration.get("payment_date"),
            "date_conflict": arbitration["date_conflict"],
            "amount_missing": arbitration["amount_missing"],
            "net_amount_rmb": arbitration.get("net_amount_rmb"),
            "message": arbitration.get("message", ""),
        }

    # ================================================================
    #  批量处理
    # ================================================================

    def process_all_due(self, current_time_str: str = None) -> list[dict]:
        """处理所有到期需要轮询的股票"""
        if current_time_str is None:
            current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        due_stocks = db.get_stocks_due_for_check(current_time_str)
        if not due_stocks:
            logger.info("没有需要轮询的股票")
            return []

        results = []
        for stock in due_stocks:
            try:
                result = self.process_stock(stock["stock_code"])
                results.append(result)
            except Exception as e:
                logger.error(f"处理 {stock['stock_code']} 失败: {e}")
                results.append({
                    "stock_code": stock["stock_code"],
                    "status": "error",
                    "message": str(e),
                })
        return results

    # ================================================================
    #  仲裁逻辑（核心）
    # ================================================================

    def _arbitrate(self, data_a: Optional[DividendData],
                   data_b: Optional[DividendData]) -> dict:
        """
        双源对比仲裁。

        返回 dict:
          - status: "ok" | "date_conflict" | "no_date" | "no_data"
          - payment_date, dividend_per_share, dividend_currency
          - date_conflict: bool
          - amount_missing: bool
          - data_source: str
          - message: str
          - net_amount_rmb: float (仅 status=="ok" 时)
        """
        base = {
            "status": "no_data",
            "payment_date": None,
            "dividend_per_share": None,
            "dividend_currency": "CNY",
            "announcement_date": None,
            "record_date": None,
            "ex_dividend_date": None,
            "date_conflict": False,
            "amount_missing": False,
            "data_source": "none",
            "message": "",
            "net_amount_rmb": None,
        }

        # --- 两个源都没数据 ---
        if not data_a and not data_b:
            base["message"] = "两个数据源均未查到分红数据"
            base["status"] = "no_data"
            return base

        # --- 仅一个源有数据 ---
        if data_a and not data_b:
            return self._single_source_result(data_a, "仅 AkShare 有数据，新浪无数据")
        if data_b and not data_a:
            return self._single_source_result(data_b, "仅新浪有数据，AkShare 无数据")

        # --- 两个源都有数据 → 交叉验证 ---
        result = base
        result["data_source"] = "both"

        # ★ 日期对比 ★
        # 原则: 同类型日期做对比，不混合 payment_date 和 record_date
        pay_a = data_a.payment_date
        pay_b = data_b.payment_date
        rec_a = data_a.record_date
        rec_b = data_b.record_date

        pay_conflict = False
        rec_conflict = False

        if pay_a and pay_b and pay_a != pay_b:
            pay_conflict = True
        if rec_a and rec_b and rec_a != rec_b:
            rec_conflict = True

        if pay_conflict or rec_conflict:
            result["status"] = "date_conflict"
            result["payment_date"] = pay_a or pay_b
            result["date_conflict"] = True
            detail = []
            if pay_conflict:
                detail.append(f"派息日: AkShare={pay_a}, 新浪={pay_b}")
            if rec_conflict:
                detail.append(f"登记日: AkShare={rec_a}, 新浪={rec_b}")
            result["message"] = "日期不一致！" + "; ".join(detail) + "。需人工确认。"
            self._save_conflict_info(data_a, data_b)
            return result

        # 确定最终派息日
        result["payment_date"] = pay_a or pay_b
        if result["payment_date"]:
            result["status"] = "ok"
            result["date_conflict"] = False
        elif rec_a or rec_b:
            # 有登记日但无派息日 → 算有日期，但备注
            result["status"] = "ok"
            result["date_conflict"] = False
        else:
            result["status"] = "no_date"
            result["date_conflict"] = False
            result["message"] = "两个数据源均无派息日期，需手动输入"
            return result

        # --- 填充完整日期信息（优先 AkShare） ---
        primary = data_a if data_a else data_b
        result["announcement_date"] = primary.announcement_date
        result["record_date"] = primary.record_date
        result["ex_dividend_date"] = primary.ex_dividend_date

        # ★ 金额处理 ★
        amount_a = data_a.dividend_per_share if data_a else None
        amount_b = data_b.dividend_per_share if data_b else None

        if amount_a and amount_b:
            # 两者都有金额，随机取一个（不阻塞）
            result["dividend_per_share"] = random.choice([amount_a, amount_b])
            result["amount_missing"] = False
        elif amount_a:
            result["dividend_per_share"] = amount_a
            result["amount_missing"] = False
        elif amount_b:
            result["dividend_per_share"] = amount_b
            result["amount_missing"] = False
        else:
            # 两个源都没有金额（罕见）
            result["amount_missing"] = True
            result["dividend_per_share"] = None

        result["dividend_currency"] = primary.dividend_currency
        result["message"] = "双源数据一致 ✓" if not result["date_conflict"] else result["message"]
        return result

    def _single_source_result(self, data: DividendData, msg: str) -> dict:
        """仅单一源有数据时的处理"""
        result = {
            "status": "no_date",
            "payment_date": data.payment_date,
            "dividend_per_share": data.dividend_per_share,
            "dividend_currency": data.dividend_currency,
            "announcement_date": data.announcement_date,
            "record_date": data.record_date,
            "ex_dividend_date": data.ex_dividend_date,
            "date_conflict": False,
            "amount_missing": data.dividend_per_share is None,
            "data_source": data.source_name,
            "message": msg,
            "net_amount_rmb": None,
        }
        if data.payment_date:
            result["status"] = "ok"
        return result

    # ================================================================
    #  存储
    # ================================================================

    def _save_result(self, stock: dict, arbitration: dict):
        """将仲裁结果写入数据库"""
        stock_code = stock["stock_code"]

        # 写入分红记录（自动更新清除手动数据）
        db.upsert_dividend_record(
            stock_code=stock_code,
            announcement_date=arbitration.get("announcement_date"),
            record_date=arbitration.get("record_date"),
            ex_dividend_date=arbitration.get("ex_dividend_date"),
            payment_date=arbitration.get("payment_date"),
            dividend_per_share=arbitration.get("dividend_per_share"),
            dividend_currency=arbitration.get("dividend_currency", "CNY"),
            data_source=arbitration.get("data_source"),
            date_conflict=1 if arbitration["date_conflict"] else 0,
            amount_missing=1 if arbitration["amount_missing"] else 0,
            next_payment_date=None,
            next_dividend_per_share=None,
            net_amount_rmb=arbitration.get("net_amount_rmb"),
            notes=arbitration.get("message"),
        )

        # 如果日期确认且无冲突，计算税后净额
        if (arbitration["status"] == "ok"
                and arbitration.get("payment_date")
                and arbitration.get("dividend_per_share")
                and not arbitration["date_conflict"]):
            self._calculate_and_store_net(stock, arbitration, stock_code)

    def _calculate_and_store_net(self, stock: dict, arbitration: dict,
                                  stock_code: str):
        """计算税后净到账，更新数据库和 arbitration dict"""
        try:
            market = stock["market"]
            tax_bracket = stock.get("tax_bracket", "over_1_year")

            if market == "HK":
                is_h = self._is_h_share(stock_code)
                tax_result = calculate_net(
                    shares_held=stock["shares_held"],
                    dividend_per_share=arbitration["dividend_per_share"],
                    market=market,
                    is_h_share=is_h,
                )
            else:
                tax_result = calculate_net(
                    shares_held=stock["shares_held"],
                    dividend_per_share=arbitration["dividend_per_share"],
                    market=market,
                    tax_bracket=tax_bracket,
                )

            net = round(tax_result.net_amount_rmb, 2)
            arbitration["net_amount_rmb"] = net
            db.upsert_dividend_record(
                stock_code=stock_code,
                net_amount_rmb=net,
            )
        except Exception as e:
            logger.error(f"计算净到账失败 {stock_code}: {e}")

    def _save_conflict_info(self, data_a: DividendData, data_b: DividendData):
        """保存冲突详情到数据库 notes 字段（供 Streamlit 展示）"""
        info = (f"日期冲突: AkShare={data_a.payment_date or data_a.record_date}, "
                f"新浪={data_b.payment_date or data_b.record_date}")
        # info 会被写入 dividend_records，在 process_stock 中通过
        # _save_result 的 notes 字段自动入库
        logger.warning(info)

    def _update_polling_after_fetch(self, stock_code: str, arbitration: dict):
        """根据仲裁结果更新轮询状态"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today = datetime.now().strftime("%Y-%m-%d")

        if arbitration["status"] == "ok" and arbitration.get("payment_date"):
            pay_date = arbitration["payment_date"]
            if pay_date < today:
                # 派息日已过 → 重置，等待下一次分红
                db.update_polling_state(stock_code, state="not_announced",
                                        last_checked_at=now)
                logger.info(f"{stock_code} 派息日 {pay_date} 已过，重置为未公布")
            else:
                # 派息日未到 → 锁定
                db.update_polling_state(stock_code, state="payment_locked",
                                        last_checked_at=now)
        elif arbitration["status"] == "no_data":
            db.update_polling_state(stock_code, last_checked_at=now)
        elif arbitration["status"] == "date_conflict":
            db.update_polling_state(stock_code, state="announced_no_date",
                                    last_checked_at=now)
        elif arbitration["status"] == "no_date":
            # 无日期 → 保持 announced_no_date，每日继续查
            db.update_polling_state(stock_code, state="announced_no_date",
                                    last_checked_at=now)

    @staticmethod
    def _is_h_share(stock_code: str) -> bool:
        """尝试从历史分红记录判断是否为 H 股"""
        try:
            data = AkShareFetcher().fetch_dividend(stock_code)
            if data and data.source_name == "akshare":
                return AkShareFetcher._check_if_h_share(stock_code)
        except Exception:
            pass
        return False
