"""
企业微信群机器人 Webhook 推送模块

纯文本消息，兼容企业微信 + 微信插件。
"""

import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


def _send_text(webhook_url: str, content: str) -> bool:
    if not webhook_url:
        return False
    payload = {"msgtype": "text", "text": {"content": content}}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        data = resp.json()
        if data.get("errcode") == 0:
            logger.info("企业微信推送成功")
            return True
        else:
            logger.error(f"企业微信推送失败: {data}")
            return False
    except Exception as e:
        logger.error(f"企业微信推送异常: {e}")
        return False


def send_dividend_reminder(
    webhook_url: str,
    stock_name: str,
    stock_code: str,
    payment_date: str,
    dividend_per_share: float,
    market: str,
    currency: str = "CNY",
) -> bool:
    market_label = "A股" if market == "A" else "港股通"
    currency_label = "元" if currency == "CNY" else "港元"

    content = (
        f"【分红到账提醒】\n"
        f"{stock_name} ({stock_code})\n"
        f"市场: {market_label}\n"
        f"派息到账日: {payment_date}\n"
        f"每股派息: {dividend_per_share:.4f} {currency_label}\n\n"
        f"请及时将分红现金再投资，复利滚雪球！"
    )
    return _send_text(webhook_url, content)


def send_manual_review_needed(
    webhook_url: str, stock_name: str, stock_code: str, reason: str,
) -> bool:
    content = (
        f"【分红数据需人工确认】\n"
        f"{stock_name} ({stock_code})\n"
        f"原因: {reason}\n\n"
        f"请打开管理界面确认或修正分红数据。"
    )
    return _send_text(webhook_url, content)


def send_error_notification(webhook_url: str, error_message: str) -> bool:
    content = (
        f"【分红提醒系统异常】\n"
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"错误: {error_message}"
    )
    return _send_text(webhook_url, content)


def send_daily_summary(
    webhook_url: str,
    upcoming_payments: list[dict],
    conflicts: list[dict],
) -> bool:
    lines = [
        f"【分红提醒日报】",
        datetime.now().strftime("%Y-%m-%d"),
        "",
    ]
    if upcoming_payments:
        lines.append("--- 近期派息 ---")
        for p in upcoming_payments:
            lines.append(
                f"{p['stock_name']} ({p['stock_code']}): "
                f"{p['payment_date']} | 净到账 {p.get('net_amount_rmb', 0) or 0:,.2f} RMB"
            )
    else:
        lines.append("近期无派息")

    if conflicts:
        lines.append("\n--- 需人工确认 ---")
        for c in conflicts:
            lines.append(
                f"{c['stock_name']} ({c['stock_code']}): {c.get('notes', '日期冲突')}"
            )

    return _send_text(webhook_url, "\n".join(lines))


def test_webhook(webhook_url: str) -> bool:
    return _send_text(
        webhook_url,
        f"【分红提醒系统】\n"
        f"连接成功\n"
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"你将在此收到分红到账提醒。",
    )
