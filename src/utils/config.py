"""
配置加载模块 — 读取 config/settings.yaml
"""

import os
import yaml

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "config")
DEFAULT_CONFIG_PATH = os.path.join(CONFIG_DIR, "settings.yaml")


def load_config(path: str = None) -> dict:
    """
    加载 YAML 配置文件。
    若文件不存在，返回默认空配置。
    """
    path = path or DEFAULT_CONFIG_PATH
    if not os.path.exists(path):
        return _default_config()
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    # 深层合并默认值
    defaults = _default_config()
    for k, v in defaults.items():
        if k not in config:
            config[k] = v
        elif isinstance(v, dict) and isinstance(config.get(k), dict):
            # 合并子 key
            for sk, sv in v.items():
                if sk not in config[k]:
                    config[k][sk] = sv
    return config


def save_config(config: dict, path: str = None):
    """保存配置到 YAML 文件"""
    path = path or DEFAULT_CONFIG_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)


def _default_config() -> dict:
    return {
        "wechat_webhook_url": "",
        "portfolio": {
            "default_a_share_tax_bracket": "over_1_year",
        },
        "polling": {
            "earnings_season_months": [3, 4, 8, 9],
            "earnings_season_interval_hours": 24,
            "off_season_interval_hours": 168,
        },
        "exchange_rate": {"source": "sina"},
        "ics": {
            "advance_days": 1,
            "advance_time": "20:00",
            "payment_day_time": "09:15",
        },
        "data_sources": {
            "primary": "akshare",
            "secondary": "sina",
            "timeout_seconds": 30,
        },
    }
