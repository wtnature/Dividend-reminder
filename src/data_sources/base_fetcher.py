"""
分红数据获取 — 基础接口定义
所有数据源必须实现此接口
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date as DateType
from typing import Optional


@dataclass
class DividendData:
    """分红数据结构（所有数据源统一输出格式）"""
    stock_code: str                          # e.g. "600900.SH"
    stock_name: str                          # e.g. "长江电力"
    market: str                              # "A" or "HK"
    announcement_date: Optional[str] = None  # 预案公告日
    record_date: Optional[str] = None        # 股权登记日
    ex_dividend_date: Optional[str] = None   # 除权除息日
    payment_date: Optional[str] = None       # 派息到账日 ★核心
    dividend_per_share: Optional[float] = None   # 每股派息（原始币种）
    dividend_currency: str = "CNY"               # 币种
    source_name: str = ""                         # 数据来源名称
    raw_data: dict = field(default_factory=dict)  # 原始返回数据（调试用）


@dataclass
class StockInfo:
    """股票基本信息"""
    stock_code: str       # e.g. "600900.SH"
    stock_name: str       # e.g. "长江电力"
    market: str           # "A" or "HK"
    is_h_share: bool = False  # 仅港股有效：是否为H股


class BaseFetcher(ABC):
    """数据源抽象基类"""

    source_name: str = "base"

    @abstractmethod
    def validate_stock_code(self, code: str) -> Optional[StockInfo]:
        """校验股票代码，返回 StockInfo 或 None"""
        ...

    @abstractmethod
    def fetch_dividend(self, stock_code: str) -> Optional[DividendData]:
        """
        获取最新分红数据。
        返回 None 表示未查到任何数据。
        """
        ...

    def normalize_code(self, code: str, market: str) -> str:
        """标准化股票代码格式 e.g. 600900 + A → 600900.SH"""
        code = code.strip().upper()
        if "." in code:
            return code
        suffix = {("A", "SH"): ".SH", ("A", "SZ"): ".SZ",
                   ("HK",): ".HK"}
        if market == "A":
            if code.startswith(("6", "9")):
                return f"{code}.SH"
            elif code.startswith(("0", "3")):
                return f"{code}.SZ"
            return f"{code}.SH"
        elif market == "HK":
            return f"{code}.HK"
        return code
