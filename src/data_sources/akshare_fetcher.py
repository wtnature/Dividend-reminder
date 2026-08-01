"""
AkShare 数据获取器

A股分红: akshare.stock_dividend_cninfo(symbol="600900")
港股分红: akshare.stock_hk_dividend_payout_em(symbol="00700")
股票信息: akshare.stock_individual_info_em(symbol="600900")

注意: 国内金融站点需直连，不能走代理。本模块会自动清除代理环境变量。
"""

import logging
import os
import re
from typing import Optional

from .base_fetcher import BaseFetcher, DividendData, StockInfo

logger = logging.getLogger(__name__)


class AkShareFetcher(BaseFetcher):
    source_name = "akshare"

    def __init__(self):
        self._ensure_no_proxy()

    @staticmethod
    def _ensure_no_proxy():
        """国内金融 API 必须直连，清除代理"""
        for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                   "ALL_PROXY", "all_proxy"]:
            os.environ.pop(k, None)

    # ---- 股票校验 ----

    def validate_stock_code(self, code: str) -> Optional[StockInfo]:
        """
        校验股票代码:
        1. 用分红接口验证代码有效性
        2. 用新浪获取中文名称
        3. 港股额外判断 H 股
        """
        code = code.strip()
        market = self._guess_market(code)
        clean = code.replace(".SH", "").replace(".SZ", "").replace(".HK", "")
        full_code = self.normalize_code(clean, market)

        # 获取中文名称 — 优先新浪（不依赖代理），次选 AkShare
        name = self._get_name_sina(clean, market)
        if not name:
            name = self._get_name_akshare(clean, market)

        # 校验代码有效性 — 能拉到分红数据 = 有效
        data = self.fetch_dividend(full_code)
        if data:
            return StockInfo(
                stock_code=full_code,
                stock_name=name or clean,
                market=market,
                is_h_share=(self._check_if_h_share(full_code) if market == "HK" else False),
            )

        # 无分红数据但有名称 → 仍可添加（可能是新股）
        if name:
            return StockInfo(
                stock_code=full_code,
                stock_name=name,
                market=market,
                is_h_share=(self._check_if_h_share(full_code) if market == "HK" else False),
            )

        return None

    @staticmethod
    def _get_name_sina(clean_code: str, market: str) -> Optional[str]:
        """通过新浪行情 API 获取股票名称"""
        try:
            import requests
            syms = [f"sh{clean_code}", f"sz{clean_code}"] if market == "A" else [f"hk{clean_code}"]
            for sym in syms:
                url = f"https://hq.sinajs.cn/list={sym}"
                resp = requests.get(url, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://finance.sina.com.cn",
                }, timeout=10)
                resp.encoding = "gbk"
                text = resp.text.strip()
                if text and '=""' not in text:
                    import re
                    m = re.search(r'"(.+)"', text)
                    if m:
                        fields = m.group(1).split(",")
                        if len(fields) > 1 and fields[0]:
                            return fields[0]
        except Exception:
            pass
        return None

    @staticmethod
    def _get_name_akshare(clean_code: str, market: str) -> Optional[str]:
        """通过 AkShare 获取股票名称（备选，可能受代理影响）"""
        try:
            import akshare as ak
            if market == "A":
                df = ak.stock_individual_info_em(symbol=clean_code)
                if df is not None and not df.empty:
                    info = dict(zip(df["item"], df["value"]))
                    return info.get("股票简称")
            elif market == "HK":
                df = ak.stock_hk_dividend_payout_em(symbol=clean_code)
                if df is not None and not df.empty:
                    for col in ["名称", "股票简称"]:
                        if col in df.columns:
                            return str(df[col].iloc[0])
        except Exception:
            pass
        return None

    # ---- 分红数据 ----

    def fetch_dividend(self, stock_code: str) -> Optional[DividendData]:
        try:
            import akshare as ak
        except ImportError:
            return None

        self._ensure_no_proxy()
        market = self._guess_market(stock_code)
        try:
            if market == "A":
                return self._fetch_a_share(ak, stock_code)
            elif market == "HK":
                return self._fetch_hk_share(ak, stock_code)
        except Exception as e:
            logger.warning(f"AkShare 获取 {stock_code} 分红数据失败: {e}")
            return None

    # ---- A 股 ----

    def _fetch_a_share(self, ak, stock_code: str) -> Optional[DividendData]:
        """
        使用 stock_dividend_cninfo (巨潮资讯网) 获取 A 股分红。
        字段: 实施公告发布日期, 股权登记日, 除权日, 派息日, 派息比例(每10股), 实施方案分红说明
        """
        clean = stock_code.replace(".SH", "").replace(".SZ", "")
        df = ak.stock_dividend_cninfo(symbol=clean)
        if df is None or df.empty:
            return None

        # 最后一行 = 最新分红
        latest = df.iloc[-1]

        # 每股派息: 派息比例是"每10股派X元"，除以10得到每股
        dps_per_10 = self._safe_float(latest.get("派息比例"))
        dps = dps_per_10 / 10 if dps_per_10 else None

        if dps is None or dps == 0:
            return None

        return DividendData(
            stock_code=stock_code,
            stock_name="",
            market="A",
            announcement_date=self._safe_date(latest, "实施方案公告日期"),
            record_date=self._safe_date(latest, "股权登记日"),
            ex_dividend_date=self._safe_date(latest, "除权日"),
            payment_date=self._safe_date(latest, "派息日"),
            dividend_per_share=dps,
            dividend_currency="CNY",
            source_name=self.source_name,
            raw_data=latest.to_dict() if hasattr(latest, "to_dict") else {},
        )

    # ---- 港股 ----

    def _fetch_hk_share(self, ak, stock_code: str) -> Optional[DividendData]:
        """
        港股分红: stock_hk_dividend_payout_em
        """
        clean = stock_code.replace(".HK", "")
        df = ak.stock_hk_dividend_payout_em(symbol=clean)
        if df is None or df.empty:
            return None

        latest = df.iloc[0]
        # 列名可能为英文/中文
        dps = (self._safe_float(latest.get("每股派息")) or
               self._safe_float(latest.get("dividendPerShare")) or
               self._safe_float(latest.get("DividendPerShare")) or
               self._safe_float(latest.get("派息")))

        if dps is None or dps == 0:
            return None

        return DividendData(
            stock_code=stock_code,
            stock_name=str(latest.get("名称", latest.get("name", ""))),
            market="HK",
            announcement_date=(self._safe_date(latest, "公告日期") or
                               self._safe_date(latest, "AnnounceDate")),
            record_date=(self._safe_date(latest, "股权登记日") or
                         self._safe_date(latest, "RecordDate")),
            ex_dividend_date=(self._safe_date(latest, "除权除息日") or
                              self._safe_date(latest, "ExDividendDate")),
            payment_date=(self._safe_date(latest, "派息日") or
                          self._safe_date(latest, "PaymentDate")),
            dividend_per_share=dps,
            dividend_currency="HKD",
            source_name=self.source_name,
            raw_data=latest.to_dict() if hasattr(latest, "to_dict") else {},
        )

    # ---- helpers ----

    @staticmethod
    def _guess_market(code: str) -> str:
        code = code.strip().upper()
        if code.endswith(".HK") or (code.isdigit() and len(code) == 5):
            return "HK"
        return "A"

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_date(row, key: str) -> Optional[str]:
        import pandas as pd
        val = row.get(key)
        if val is None:
            return None
        if isinstance(val, float) and pd.isna(val):
            return None
        try:
            ts = pd.Timestamp(val)
            return ts.strftime("%Y-%m-%d")
        except Exception:
            s = str(val).strip()[:10]
            if re.match(r"\d{4}-\d{2}-\d{2}", s):
                return s
            return None

    @staticmethod
    def _check_if_h_share(stock_code: str) -> bool:
        try:
            import akshare as ak
            clean = stock_code.replace(".HK", "")
            info_df = ak.stock_hk_company_profile_em(symbol=clean)
            if info_df is not None and not info_df.empty:
                profile = dict(zip(info_df["item"], info_df["value"]))
                place = str(profile.get("注册地", profile.get("公司注册地", "")))
                return any(kw in place for kw in ["中国", "内地", "大陆"])
        except Exception:
            pass
        return False
