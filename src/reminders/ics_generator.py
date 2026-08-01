"""
ICS 日历文件生成器

生成标准 iCalendar (.ics) 文件，用户订阅 GitHub Raw URL 后：
  - 派息日前 1 天 20:00  → 提前提醒
  - 派息日当天 09:15      → 系统级响铃闹钟

存储位置: output/dividend.ics
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from datetime import timedelta

from icalendar import Calendar, Event, Alarm

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "dividend.ics")


def generate_ics(upcoming_payments: list[dict],
                 config: dict,
                 output_path: str = None) -> Optional[str]:
    """
    生成 ICS 日历文件。

    Args:
        upcoming_payments: db.get_upcoming_payments() 返回的派息列表
        config: settings.yaml 完整配置
        output_path: 输出路径，默认 output/dividend.ics

    Returns:
        输出文件路径，无数据则返回 None
    """
    if not upcoming_payments:
        logger.info("无近期派息数据，跳过 ICS 生成")
        return None

    ics_cfg = config.get("ics", {})
    advance_days = ics_cfg.get("advance_days", 1)
    advance_time = ics_cfg.get("advance_time", "20:00")
    payment_day_time = ics_cfg.get("payment_day_time", "09:15")

    cal = Calendar()
    cal.add("prodid", "-//Dividend Reminder// dividend.ics //CN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "分红提醒")
    cal.add("x-wr-caldesc", "A股/港股通 分红到账提醒")
    cal.add("x-published-ttl", "PT4H")  # iPhone 每 4 小时刷新

    for payment in upcoming_payments:
        # 优先使用 effective 字段（含手动数据）
        payment_date_str = (payment.get("effective_payment_date")
                            or payment.get("payment_date"))
        if not payment_date_str:
            continue

        try:
            payment_date = datetime.strptime(payment_date_str, "%Y-%m-%d")
        except ValueError:
            logger.warning(f"无法解析日期: {payment_date_str}")
            continue

        stock_name = payment.get("stock_name", "")
        stock_code = payment.get("stock_code", "")
        net_rmb = payment.get("net_amount_rmb") or 0
        dividend_per_share = (payment.get("effective_dps")
                              or payment.get("dividend_per_share") or 0)
        market = payment.get("market", "A")
        currency = "元" if (payment.get("dividend_currency", "CNY") == "CNY") else "港元"

        market_label = "A股" if market == "A" else "港股通"

        # --- 事件 1: 派息日前一天提醒 ---
        advance_date = payment_date - timedelta(days=advance_days)
        advance_hour, advance_minute = _parse_time(advance_time)
        advance_dt = advance_date.replace(hour=advance_hour, minute=advance_minute)

        cal.add_component(_make_event(
            summary=f"📅 明天分红到账: {stock_name}",
            description=(
                f"{stock_name} ({stock_code}) 将于明天 ({payment_date_str}) 派息到账。\n"
                f"市场: {market_label}\n"
                f"每股派息: {dividend_per_share:.4f} {currency}\n"
                f"预估净到账: ¥{net_rmb:,.2f} RMB\n\n"
                f"请准备再投资计划。"
            ),
            dt=advance_dt,
            alarm_trigger="-PT30M",  # 提前 30 分钟提醒
        ))

        # --- 事件 2: 派息日当天 09:15 闹钟 ---
        pay_hour, pay_minute = _parse_time(payment_day_time)
        pay_dt = payment_date.replace(hour=pay_hour, minute=pay_minute)

        cal.add_component(_make_event(
            summary=f"💰 分红到账！{stock_name}",
            description=(
                f"{stock_name} ({stock_code}) 分红现金今日到账。\n"
                f"市场: {market_label}\n"
                f"每股派息: {dividend_per_share:.4f} {currency}\n"
                f"预估净到账: ¥{net_rmb:,.2f} RMB\n\n"
                f"🔔 请将分红现金再投资，复利滚雪球！"
            ),
            dt=pay_dt,
            alarm_trigger="-PT0M",  # 准时响铃
        ))

    # --- 写入文件 ---
    os.makedirs(os.path.dirname(output_path or OUTPUT_FILE), exist_ok=True)
    out = output_path or OUTPUT_FILE

    with open(out, "wb") as f:
        f.write(cal.to_ical())

    logger.info(f"ICS 文件已生成: {out} ({len(upcoming_payments)} 只股票)")
    return out


def _make_event(summary: str, description: str,
                dt: datetime, alarm_trigger: str = "-PT30M") -> Event:
    """创建一个带闹钟的日历事件"""
    event = Event()
    event.add("summary", summary)
    event.add("description", description)
    event.add("dtstart", dt)
    event.add("dtend", dt + timedelta(minutes=15))
    event.add("transp", "TRANSPARENT")  # 不阻塞其他日程

    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", summary)
    alarm.add("trigger", _parse_trigger(alarm_trigger))
    event.add_component(alarm)

    return event


def _parse_time(time_str: str) -> tuple[int, int]:
    """解析 HH:MM 字符串"""
    try:
        h, m = time_str.split(":")
        return int(h), int(m)
    except Exception:
        return 9, 15


def _parse_trigger(trigger_str: str) -> timedelta:
    """解析 '-PT30M' → timedelta(minutes=-30)"""
    import re
    neg = trigger_str.startswith("-")
    m = re.search(r"(\d+)M", trigger_str)
    minutes = int(m.group(1)) if m else 0
    m = re.search(r"(\d+)H", trigger_str)
    hours = int(m.group(1)) if m else 0
    td = timedelta(hours=hours, minutes=minutes)
    return -td if neg else td


def get_subscribe_url(repo_owner: str, repo_name: str, branch: str = "main") -> str:
    """
    生成 GitHub Raw URL 订阅地址。

    用法: 将此 URL 粘贴到 iPhone 设置 → 日历 → 账户 → 添加账户 → 其他 → 添加已订阅的日历
    """
    return (f"https://raw.githubusercontent.com/{repo_owner}/{repo_name}"
            f"/{branch}/output/dividend.ics")
