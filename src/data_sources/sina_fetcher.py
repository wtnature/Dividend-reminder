"""
新浪财经数据获取器

A股分红: 解析新浪分红送配页面 (pd.read_html)
港股分红: 港交所披露易 (HTML 表格)
汇率数据: 新浪外汇行情

限制: 新浪分红表无「派息日」字段，仅有公告日/股权登记日/除权除息日。
      派息日以 AkShare 为准。
"""

import logging
import os
import re
from typing import Optional

import pandas as pd
import requests

from .base_fetcher import BaseFetcher, DividendData, StockInfo

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn",
}


class SinaFetcher(BaseFetcher):
    source_name = "sina"

    # ---- 股票校验 ----

    def validate_stock_code(self, code: str) -> Optional[StockInfo]:
        code = code.strip()
        market = self._guess_market(code)
        symbols = self._build_sina_symbols(code, market)

        for sym in symbols:
            url = f"https://hq.sinajs.cn/list={sym}"
            try:
                resp = requests.get(url, headers=HEADERS, timeout=10)
                resp.encoding = "gbk"
                text = resp.text.strip()
                if text and '=""' not in text:
                    m = re.search(r'"(.+)"', text)
                    if m:
                        fields = m.group(1).split(",")
                        if len(fields) > 1 and fields[0]:
                            return StockInfo(
                                stock_code=self.normalize_code(code, market),
                                stock_name=fields[0],
                                market=market,
                            )
            except Exception as e:
                logger.debug(f"新浪校验 {sym} 异常: {e}")
                continue
        return None

    # ---- 分红数据 ----

    def fetch_dividend(self, stock_code: str) -> Optional[DividendData]:
        market = self._guess_market(stock_code)
        if market == "A":
            return self._fetch_a_share(stock_code)
        elif market == "HK":
            return self._fetch_hk_share(stock_code)

    def _fetch_a_share(self, stock_code: str) -> Optional[DividendData]:
        """
        解析新浪 A 股分红送配页面。
        分红表为多级列索引 (MultiIndex)，包含:
          公告日期 / 股权登记日 / 除权除息日 / 派息(税前)
        注意: 新浪无「派息到账日」，payment_date 返回 None
        """
        clean = stock_code.replace(".SH", "").replace(".SZ", "")
        url = (f"https://vip.stock.finance.sina.com.cn/corp/go.php/"
               f"vISSUE_ShareBonus/stockid/{clean}.phtml")

        try:
            tables = pd.read_html(url, encoding="gbk")
        except Exception as e:
            logger.warning(f"新浪 A 股分红页面解析失败 {stock_code}: {e}")
            return None

        # 找分红表格 — 有 9 列且包含多级列头的那个
        div_table = None
        for t in tables:
            if t.shape[1] >= 7 and isinstance(t.columns, pd.MultiIndex):
                div_table = t
                break
            elif t.shape[1] >= 7 and not isinstance(t.columns, pd.MultiIndex):
                # 有些页面不是多级列头
                div_table = t
                break

        if div_table is None:
            return None

        # 取第一行 = 最新分红
        latest = div_table.iloc[0]

        # 提取字段 — 兼容多级列头和普通列头
        col_names = [str(c[-1]) if isinstance(c, tuple) else str(c)
                     for c in div_table.columns]

        def col_val(keywords: list[str]) -> Optional[str]:
            for kw in keywords:
                for i, name in enumerate(col_names):
                    if kw in name:
                        return self._safe_cell(latest.iloc[i])
            return None

        announcement = col_val(["公告日期"])
        record = col_val(["股权登记日"])
        ex_div = col_val(["除权除息日"])
        dps_per_10 = col_val(["派息(税前)", "派息", "税前"])
        status = col_val(["进度"])

        # 只取"实施"状态的记录
        if status and "实施" not in str(status):
            # 尝试找最近一条已实施的
            for idx in range(min(5, len(div_table))):
                row = div_table.iloc[idx]
                s = self._safe_cell(row.iloc[4]) if len(row) > 4 else ""
                if s and "实施" in str(s):
                    latest = row
                    announcement = col_val(["公告日期"])
                    record = col_val(["股权登记日"])
                    ex_div = col_val(["除权除息日"])
                    dps_per_10 = col_val(["派息(税前)", "派息", "税前"])
                    break

        if dps_per_10 is None:
            return None

        dps = self._safe_float(dps_per_10)
        if dps is None:
            return None
        dps = dps / 10

        return DividendData(
            stock_code=stock_code,
            stock_name="",
            market="A",
            announcement_date=announcement,
            record_date=record,
            ex_dividend_date=ex_div,
            payment_date=None,  # 新浪无派息日
            dividend_per_share=dps,
            dividend_currency="CNY",
            source_name=self.source_name,
            raw_data={},
        )

    def _fetch_hk_share(self, stock_code: str) -> Optional[DividendData]:
        """港股分红暂依赖 AkShare，新浪港股页面格式不稳定"""
        return None

    # ---- 辅助 ----

    @staticmethod
    def _guess_market(code: str) -> str:
        code = code.strip().upper()
        if code.endswith(".HK") or (code.isdigit() and len(code) == 5):
            return "HK"
        return "A"

    @staticmethod
    def _build_sina_symbols(code: str, market: str) -> list[str]:
        clean = code.replace(".SH", "").replace(".SZ", "").replace(".HK", "")
        if market == "A":
            return [f"sh{clean}", f"sz{clean}"]
        elif market == "HK":
            return [f"hk{clean}"]
        return [clean]

    @staticmethod
    def _safe_cell(val) -> Optional[str]:
        """安全的从单元格取值"""
        if val is None:
            return None
        try:
            if isinstance(val, float) and pd.isna(val):
                return None
        except Exception:
            pass
        s = str(val).strip()
        if s in ("--", "---", "nan", ""):
            return None
        # 日期格式检测
        if re.match(r"\d{4}-\d{2}-\d{2}", s):
            return s
        return s

    @staticmethod
    def _safe_date(val) -> Optional[str]:
        """提取日期字符串"""
        result = SinaFetcher._safe_cell(val)
        if result and re.match(r"\d{4}-\d{2}-\d{2}", result):
            return result
        return None

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None


# ============================================================
#  新浪汇率接口
# ============================================================

def fetch_hkd_cny_rate() -> Optional[float]:
    """从新浪外汇 API 获取港元兑人民币汇率"""
    try:
        url = "https://hq.sinajs.cn/list=fx_sgdhkdcny"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "gbk"
        text = resp.text.strip()
        m = re.search(r'"(.+)"', text)
        if m:
            fields = m.group(1).split(",")
            if len(fields) >= 7:
                return float(fields[6])
    except Exception as e:
        logger.warning(f"获取汇率失败: {e}")
    return None
