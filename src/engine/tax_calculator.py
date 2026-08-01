"""
税后净分红计算引擎

规则 A — A 股差别化红利税:
  > 1 年:          0%
  1 个月 ~ 1 年:   10%
  < 1 个月:        20%

规则 B — 港股通:
  H 股 (内地注册):      10%
  非 H 股 (红筹/本地):   20%
  × HKD/CNY 汇率

设计原则:
  - 所有金额均返回人民币(RMB)净到账估算值
  - 不接受绝对精确，接受一定误差
"""

import logging
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# A 股税率映射
A_SHARE_TAX_RATES = {
    "over_1_year": 0.0,
    "one_month_to_1_year": 0.10,
    "under_1_month": 0.20,
}

# 港股税率
HK_H_SHARE_RATE = 0.10      # H 股（内地注册）
HK_NON_H_SHARE_RATE = 0.20  # 红筹 / 香港本地


@dataclass
class TaxResult:
    """税率计算结果"""
    market: str                      # "A" or "HK"
    gross_amount_original: float     # 原始币种总金额
    original_currency: str           # 原始币种
    tax_rate: float                  # 适用税率
    tax_amount_original: float       # 扣税金额（原始币种）
    net_amount_original: float       # 税后金额（原始币种）
    exchange_rate: Optional[float]   # 汇率（仅港股通）
    net_amount_rmb: float            # 最终净到账 RMB


def calculate_a_share(shares_held: float, dividend_per_share: float,
                      tax_bracket: str = "over_1_year") -> TaxResult:
    """
    A 股税后分红计算

    Args:
        shares_held: 持有股数
        dividend_per_share: 每股税前派息 (RMB)
        tax_bracket: 红利税档位 (over_1_year / one_month_to_1_year / under_1_month)
    """
    rate = A_SHARE_TAX_RATES.get(tax_bracket, 0.0)
    gross = shares_held * dividend_per_share
    tax_amount = gross * rate
    net = gross - tax_amount

    logger.info(f"A 股计算: {shares_held}股 × {dividend_per_share}元/股 = "
                f"税前{gross:.2f} RMB, 税率{rate:.0%}, 税后{net:.2f} RMB")

    return TaxResult(
        market="A",
        gross_amount_original=gross,
        original_currency="CNY",
        tax_rate=rate,
        tax_amount_original=tax_amount,
        net_amount_original=net,
        exchange_rate=None,
        net_amount_rmb=net,
    )


def calculate_hk_share(shares_held: float, dividend_per_share_hkd: float,
                       is_h_share: bool = False,
                       exchange_rate: Optional[float] = None) -> TaxResult:
    """
    港股通税后分红计算

    Args:
        shares_held: 持有股数
        dividend_per_share_hkd: 每股派息 (HKD)
        is_h_share: 是否为 H 股（内地注册）
        exchange_rate: HKD→CNY 汇率，若为 None 则尝试自动获取
    """
    rate = HK_H_SHARE_RATE if is_h_share else HK_NON_H_SHARE_RATE
    gross_hkd = shares_held * dividend_per_share_hkd
    tax_hkd = gross_hkd * rate
    net_hkd = gross_hkd - tax_hkd

    if exchange_rate is None:
        exchange_rate = _get_hkd_cny_rate()

    if exchange_rate is None or exchange_rate <= 0:
        logger.warning("无法获取有效汇率，使用近似值 0.92")
        exchange_rate = 0.92  # fallback

    net_rmb = net_hkd * exchange_rate

    label = "H股" if is_h_share else "非H股"
    logger.info(f"港股通计算({label}): {shares_held}股 × {dividend_per_share_hkd}HKD/股 = "
                f"税前{gross_hkd:.2f} HKD, 税率{rate:.0%}, "
                f"汇率{exchange_rate:.4f}, 税后{net_rmb:.2f} RMB")

    return TaxResult(
        market="HK",
        gross_amount_original=gross_hkd,
        original_currency="HKD",
        tax_rate=rate,
        tax_amount_original=tax_hkd,
        net_amount_original=net_hkd,
        exchange_rate=exchange_rate,
        net_amount_rmb=net_rmb,
    )


def calculate_net(shares_held: float, dividend_per_share: float,
                  market: str, tax_bracket: str = "over_1_year",
                  is_h_share: bool = False,
                  exchange_rate: Optional[float] = None) -> TaxResult:
    """
    统一入口：根据 market 自动选择 A 股或港股通计算
    """
    if market == "A":
        return calculate_a_share(shares_held, dividend_per_share, tax_bracket)
    elif market == "HK":
        return calculate_hk_share(shares_held, dividend_per_share,
                                  is_h_share, exchange_rate)
    else:
        raise ValueError(f"不支持的市场: {market}")


def _get_hkd_cny_rate() -> Optional[float]:
    """尝试从新浪获取港元汇率"""
    try:
        from ..data_sources.sina_fetcher import fetch_hkd_cny_rate
        return fetch_hkd_cny_rate()
    except Exception:
        pass
    return None
