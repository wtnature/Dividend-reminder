"""
代理配置 — 国内金融数据源绕过系统代理

AkShare / 新浪财经等国内站点不需要翻墙代理。
在 import 数据源模块前调用 disable_proxy_for_domestic()。
"""

import os

# 需要直连的国内域名
DOMESTIC_DOMAINS = [
    "eastmoney.com",
    "sina.com.cn",
    "sinajs.cn",
    "finance.sina.com.cn",
    "vip.stock.finance.sina.com.cn",
    "hq.sinajs.cn",
    "qyapi.weixin.qq.com",
    "ft.qq.com",
    "sct.ftqq.com",
]


def disable_proxy_for_domestic():
    """设置 NO_PROXY 让国内金融站点走直连"""
    existing = os.environ.get("NO_PROXY", "")
    additions = ",".join(DOMESTIC_DOMAINS)
    if existing:
        os.environ["NO_PROXY"] = f"{existing},{additions}"
        os.environ["no_proxy"] = os.environ["NO_PROXY"]
    else:
        os.environ["NO_PROXY"] = additions
        os.environ["no_proxy"] = additions
