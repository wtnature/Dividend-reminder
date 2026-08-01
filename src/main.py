#!/usr/bin/env python3
"""
Dividend Reminder — CLI 入口
供 GitHub Actions 定时调用。

用法:
  python src/main.py check          # 检查到期股票并发送提醒
  python src/main.py schedule       # 更新所有股票的轮询计划
  python src/main.py ics            # 重新生成 ICS 日历文件
  python src/main.py summary        # 发送每日摘要
  python src/main.py test-wechat    # 测试企业微信连接
  python src/main.py init           # 初始化数据库
"""

import os
import sys
import logging
import argparse
from datetime import datetime

# 国内金融数据源必须直连 — 禁用 requests 读取系统代理
for _k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"

import requests as _requests
_original_session_init = _requests.Session.__init__
def _patched_init(self, *a, **kw):
    _original_session_init(self, *a, **kw)
    self.trust_env = False
_requests.Session.__init__ = _patched_init

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db import database as db
from src.engine.dividend_engine import DividendEngine
from src.engine.poll_scheduler import PollScheduler
from src.reminders.wechat_webhook import (
    send_dividend_reminder,
    send_manual_review_needed,
    send_error_notification,
    send_daily_summary,
    test_webhook,
)
from src.reminders.ics_generator import generate_ics
from src.utils.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


def _w(config: dict) -> str:
    """提取 webhook URL"""
    return config.get("wechat_webhook_url", "")


def cmd_init():
    db.init_database()
    logger.info("数据库初始化完成")


def cmd_check(config: dict):
    logger.info("=" * 50)
    logger.info(f"分红提醒检查开始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 50)

    db.init_database()
    engine = DividendEngine()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    today_str = datetime.now().strftime("%Y-%m-%d")
    webhook_url = _w(config)

    results = engine.process_all_due(now_str)
    logger.info(f"已处理 {len(results)} 只股票")

    reminders_sent = 0
    conflicts = []

    for result in results:
        stock_code = result.get("stock_code", "")
        stock_name = result.get("stock_name", "")
        status = result.get("status", "error")
        payment_date = result.get("payment_date")

        if status == "date_conflict":
            conflicts.append(result)
            send_manual_review_needed(
                webhook_url, stock_name, stock_code,
                result.get("message", "日期不一致"),
            )
        elif status == "ok" and payment_date == today_str:
            stock = db.get_stock(stock_code)
            if not stock:
                continue
            record = db.get_dividend_record(stock_code)
            if not record:
                continue
            success = send_dividend_reminder(
                webhook_url=webhook_url,
                stock_name=stock_name,
                stock_code=stock_code,
                payment_date=payment_date,
                dividend_per_share=record.get("dividend_per_share") or 0,
                market=stock["market"],
                currency=record.get("dividend_currency", "CNY"),
            )
            if success:
                reminders_sent += 1
                logger.info(f"已发送提醒: {stock_name} ({stock_code})")

    upcoming = db.get_upcoming_payments(days=90)
    if upcoming:
        ics_path = generate_ics(upcoming, config)
        if ics_path:
            logger.info(f"ICS 日历已更新: {ics_path}")

    scheduler = PollScheduler(config)
    scheduler.update_all_schedules()

    logger.info(f"提醒已发送: {reminders_sent} 条")
    logger.info(f"待处理冲突: {len(conflicts)} 项")
    logger.info("检查完成")


def cmd_schedule(config: dict):
    db.init_database()
    scheduler = PollScheduler(config)
    scheduler.update_all_schedules()
    logger.info("轮询计划已更新")


def cmd_ics(config: dict):
    db.init_database()
    upcoming = db.get_upcoming_payments(days=90)
    if upcoming:
        path = generate_ics(upcoming, config)
        if path:
            logger.info(f"ICS 文件已生成: {path}")
    else:
        logger.info("无近期派息，未生成 ICS")


def cmd_summary(config: dict):
    db.init_database()
    webhook_url = _w(config)
    upcoming = db.get_upcoming_payments(days=30)
    conflicts = db.get_dividends_needing_review()
    send_daily_summary(webhook_url, upcoming, conflicts)
    logger.info("每日摘要已发送")


def cmd_test_wechat(config: dict):
    webhook_url = _w(config)
    if not webhook_url:
        print("wechat_webhook_url 未配置")
        return
    if test_webhook(webhook_url):
        print("企业微信 Webhook 连接成功")
    else:
        print("企业微信 Webhook 连接失败")


def main():
    parser = argparse.ArgumentParser(description="Dividend Reminder")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init", help="初始化数据库")
    subparsers.add_parser("check", help="检查到期股票并发送提醒")
    subparsers.add_parser("schedule", help="更新轮询计划")
    subparsers.add_parser("ics", help="生成 ICS 日历文件")
    subparsers.add_parser("summary", help="发送每日摘要")
    subparsers.add_parser("test-wechat", help="测试企业微信连接")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    config = load_config()

    commands = {
        "init": cmd_init,
        "check": lambda: cmd_check(config),
        "schedule": lambda: cmd_schedule(config),
        "ics": lambda: cmd_ics(config),
        "summary": lambda: cmd_summary(config),
        "test-wechat": lambda: cmd_test_wechat(config),
    }

    if args.command in commands:
        try:
            commands[args.command]()
        except Exception as e:
            logger.exception(f"命令 {args.command} 执行失败: {e}")
            wh = _w(config)
            if wh:
                send_error_notification(wh, str(e))
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
