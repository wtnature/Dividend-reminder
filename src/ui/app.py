"""
Dividend Reminder — Streamlit 管理界面
"""

import os
import sys

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

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

import streamlit as st
import pandas as pd
from datetime import datetime

from src.db import database as db
from src.utils.config import load_config, save_config
from src.engine.dividend_engine import DividendEngine
from src.data_sources.akshare_fetcher import AkShareFetcher

st.set_page_config(page_title="分红提醒系统", page_icon="💰", layout="wide")
db.init_database()
config = load_config()

if "page" not in st.session_state:
    st.session_state.page = "dashboard"

# ================================================================
#  Sidebar
# ================================================================
with st.sidebar:
    st.title("分红提醒系统")
    st.caption("Dividend Reminder 0.6")
    st.divider()

    nav = st.radio(
        "导航",
        ["持仓看板", "待处理确认", "设置"],
        index=["持仓看板", "待处理确认", "设置"].index(
            {"dashboard": "持仓看板",
             "review": "待处理确认",
             "settings": "设置"}.get(st.session_state.page, "持仓看板")
        ),
        key="nav",
    )
    page_map = {"持仓看板": "dashboard", "待处理确认": "review", "设置": "settings"}
    st.session_state.page = page_map[nav]

    st.divider()
    if config.get("wechat_webhook_url"):
        st.success("微信推送已配置")
    else:
        st.warning("微信推送未配置")
    st.caption(f"更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")


# ================================================================
#  Page: 持仓看板
# ================================================================
def page_dashboard():
    st.title("持仓看板")

    stocks = db.get_all_stocks(active_only=True)
    today = datetime.now().strftime("%Y-%m-%d")
    current_month = datetime.now().month

    if stocks:
        rows = _build_rows(stocks, today, current_month)
        df = pd.DataFrame(rows)
        selection = st.dataframe(
            df, use_container_width=True, hide_index=True,
            selection_mode="multi-row", on_select="rerun", key="dash_table",
            column_config={
                "上次每股派息": st.column_config.TextColumn(width="small"),
                "下次每股派息": st.column_config.TextColumn(width="small"),
            },
        )

        selected_indices = []
        if selection is not None and hasattr(selection, "selection"):
            sel = selection.selection
            if sel and hasattr(sel, "rows"):
                selected_indices = [i for i in sel.rows if i < len(stocks)]

        if selected_indices:
            _show_stock_actions(stocks, selected_indices)

        st.divider()
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("更新全部"):
                with st.spinner(f"更新 {len(stocks)} 只股票..."):
                    engine = DividendEngine()
                    for s in stocks:
                        engine.process_stock(s["stock_code"])
                st.rerun()
        with col2:
            if st.button("发送分红摘要到微信"):
                _send_summary(rows, today)
        with col3:
            if st.button("生成 ICS 日历"):
                _generate_ics()
    else:
        st.info("还没有添加任何股票。在下方添加。")

    # ---- 添加股票（底部折叠） ----
    st.divider()
    with st.expander("添加股票"):
        code_input = st.text_input(
            "股票代码",
            placeholder="A股如 600900，港股如 00700",
            key="add_code",
        )
        if code_input and st.button("校验代码"):
            with st.spinner("查询中..."):
                fetcher = AkShareFetcher()
                info = fetcher.validate_stock_code(code_input)
                if info:
                    st.session_state.validated_code = info.stock_code
                    st.session_state.validated_name = info.stock_name
                    st.session_state.validated_market = info.market
                    if info.market == "HK":
                        st.session_state.validated_is_h = info.is_h_share
                    st.rerun()
                else:
                    st.error(f"无法校验 '{code_input}'")

        code = st.session_state.get("validated_code", "")
        name = st.session_state.get("validated_name", "")
        market = st.session_state.get("validated_market", "")
        if code:
            market_label = "A股" if market == "A" else "港股通"
            st.markdown(f"**{name}** | {code} | {market_label}")
            if st.button(f"确认添加 {name}"):
                ok = db.add_stock(stock_code=code, stock_name=name, market=market)
                if ok:
                    st.success(f"已添加 {name}")
                    for k in ["validated_code", "validated_name", "validated_market", "validated_is_h"]:
                        st.session_state.pop(k, None)
                    st.rerun()
                else:
                    st.error("添加失败，可能已存在")


# ================================================================
#  Dashboard helpers
# ================================================================
def _build_rows(stocks, today, current_month):
    rows = []
    for s in stocks:
        div_record = db.get_dividend_record(s["stock_code"])
        last_date, last_dps = "-", "-"
        next_date, next_dps = "-", "-"
        if div_record:
            # 上次 = API 的 payment_date（不动）
            pay_date = div_record.get("payment_date")
            dps = div_record.get("dividend_per_share")
            if pay_date and pay_date < today:
                last_date = pay_date
                last_dps = f"{dps:.4f}" if dps else "-"
            elif pay_date:
                last_date = "-"
                last_dps = "-"
            else:
                last_dps = f"{dps:.4f}" if dps else "-"

            # 下次 = 手动设置优先 > 未来 API 日期 > 预测
            nxt_date = div_record.get("next_payment_date")
            nxt_dps = div_record.get("next_dividend_per_share")
            if nxt_date:
                next_date = nxt_date
                next_dps = f"{nxt_dps:.4f}" if nxt_dps else "未公布"
            elif pay_date and pay_date >= today:
                next_date = pay_date
                next_dps = f"{dps:.4f}" if dps else "-"
            elif pay_date and pay_date < today:
                next_date = _next_from_last(pay_date)
                next_dps = "未公布"
            else:
                next_date = _no_date_hint(current_month)
                next_dps = "未公布"
        else:
            next_date = _no_date_hint(current_month)
            next_dps = "未公布"
        rows.append({
            "股票名称": s["stock_name"],
            "代码": s["stock_code"],
            "市场": "A股" if s["market"] == "A" else "港股",
            "上次分红日期": last_date,
            "上次每股派息": last_dps,
            "下次分红日期": next_date,
            "下次每股派息": next_dps,
        })
    return rows


def _show_stock_actions(stocks, selected_indices):
    sel_stocks = [stocks[i] for i in selected_indices]
    sel_names = [s["stock_name"] for s in sel_stocks]
    label = ", ".join(sel_names)

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button(f"更新选中 ({label})"):
            engine = DividendEngine()
            for s in sel_stocks:
                engine.process_stock(s["stock_code"])
            st.rerun()
    with col_b:
        if st.button(f"删除选中 ({label})"):
            for s in sel_stocks:
                db.delete_stock(s["stock_code"])
            st.rerun()

    with st.expander(f"手动设置下次分红 ({label})"):
        for s in sel_stocks:
            rec = db.get_dividend_record(s["stock_code"])
            cur_next_date = rec.get("next_payment_date") if rec else None
            cur_next_dps = rec.get("next_dividend_per_share") if rec else None
            c1, c2, c3 = st.columns(3)
            with c1:
                st.text(s["stock_name"])
            with c2:
                new_date = st.date_input(
                    f"下次派息日期 {s['stock_code']}",
                    value=datetime.strptime(cur_next_date, "%Y-%m-%d").date() if cur_next_date else None,
                    key=f"man_date_{s['stock_code']}",
                )
            with c3:
                new_dps = st.number_input(
                    f"下次每股派息 {s['stock_code']}",
                    value=float(cur_next_dps) if cur_next_dps else 0.0,
                    step=0.01, format="%.4f",
                    key=f"man_dps_{s['stock_code']}",
                )
            if st.button(f"保存 {s['stock_name']}", key=f"man_save_{s['stock_code']}"):
                db.upsert_dividend_record(
                    stock_code=s["stock_code"],
                    next_payment_date=new_date.strftime("%Y-%m-%d") if new_date else None,
                    next_dividend_per_share=new_dps if new_dps > 0 else None,
                )
                st.success(f"已保存 {s['stock_name']}")
                st.rerun()


def _send_summary(rows, today):
    wh = config.get("wechat_webhook_url", "")
    if not wh:
        st.warning("未配置微信")
        return
    from src.reminders.wechat_webhook import _send_text
    lines = ["【分红摘要】", f"{today}\n"]
    for r in rows:
        lines.append(
            f"{r['股票名称']} ({r['代码']})\n"
            f"下次分红: {r['下次分红日期']} | 每股派息: {r['下次每股派息']}"
        )
    if _send_text(wh, "\n\n".join(lines)):
        st.success("已发送")
    else:
        st.error("发送失败")


def _generate_ics():
    from src.reminders.ics_generator import generate_ics
    upcoming = db.get_upcoming_payments(days=90)
    if upcoming:
        path = generate_ics(upcoming, config)
        if path:
            abs_path = os.path.abspath(path)
            st.success(f"已生成: {abs_path}")
    else:
        st.info("无近期派息数据")


# ================================================================
#  Utils
# ================================================================
def _no_date_hint(month: int) -> str:
    year = datetime.now().year
    if month <= 7:
        return f"未公布，预计{year}年7月公布"
    else:
        return f"未公布，预计{year+1}年7月公布"


def _next_from_last(last_pay_date: str) -> str:
    try:
        parts = last_pay_date.split("-")
        y, m = int(parts[0]), int(parts[1])
        next_y = y + 1 if m >= 7 else y
        return f"未公布，预计{next_y}年7月公布"
    except Exception:
        return "未公布"


# ================================================================
#  Page: 待处理确认
# ================================================================
def page_review():
    st.title("待人工确认")
    st.caption("两个数据源日期不一致时，在此确认或修正。")

    conflicts = db.get_dividends_needing_review()
    if not conflicts:
        st.success("没有需要处理的项目")
        return

    for item in conflicts:
        stock_code = item["stock_code"]
        stock = db.get_stock(stock_code)
        stock_name = stock["stock_name"] if stock else stock_code

        with st.expander(
            f"{'日期冲突' if item['date_conflict'] else '金额缺失'} — {stock_name} ({stock_code})",
            expanded=True,
        ):
            st.markdown(f"备注: {item.get('notes', '无')}")
            if item["date_conflict"]:
                st.warning("两个数据源返回的派息日期不一致。")

            c1, c2, c3 = st.columns(3)
            with c1:
                new_payment = st.date_input("派息到账日", value=None, key=f"rv_p_{item['id']}")
            with c2:
                new_dps = st.number_input("每股派息", value=item.get("dividend_per_share") or 0.0,
                                          step=0.01, format="%.4f", key=f"rv_d_{item['id']}")
            with c3:
                new_announce = st.date_input("预案公告日", value=None, key=f"rv_a_{item['id']}")

            ca, cb = st.columns(2)
            with ca:
                if st.button("确认", key=f"rv_ok_{item['id']}"):
                    db.upsert_dividend_record(
                        stock_code=stock_code,
                        payment_date=new_payment.strftime("%Y-%m-%d") if new_payment else item.get("payment_date"),
                        dividend_per_share=new_dps,
                        announcement_date=new_announce.strftime("%Y-%m-%d") if new_announce else None,
                        is_manual=1,
                        date_conflict=0,
                        amount_missing=0,
                    )
                    db.update_polling_state(stock_code, state="payment_locked",
                                            last_checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    st.success("已保存")
                    st.rerun()
            with cb:
                if st.button("删除", key=f"rv_del_{item['id']}"):
                    db.update_polling_state(stock_code, state="not_announced",
                                            last_checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    st.info("已删除，下次轮询重新获取")
                    st.rerun()


# ================================================================
#  Page: 设置
# ================================================================
def page_settings():
    st.title("设置")

    st.subheader("企业微信群机器人 Webhook")
    webhook_url = st.text_input(
        "Webhook URL", value=config.get("wechat_webhook_url", ""),
        type="password",
        placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...",
        key="settings_webhook",
    )
    if st.button("保存并测试"):
        config["wechat_webhook_url"] = webhook_url
        save_config(config)
        from src.reminders.wechat_webhook import test_webhook
        if test_webhook(webhook_url):
            st.success("已保存，连接成功")
        else:
            st.error("已保存，但连接失败")

    st.divider()
    st.subheader("ICS 日历订阅")
    st.caption("将 output/dividend.ics 上传到 GitHub 后，在 iPhone 中订阅")
    st.code("https://raw.githubusercontent.com/<用户名>/<仓库>/main/output/dividend.ics")
    st.caption("iPhone: 设置 -> 日历 -> 账户 -> 添加账户 -> 其他 -> 添加已订阅的日历")


# ================================================================
#  Route
# ================================================================
pages = {"dashboard": page_dashboard, "review": page_review, "settings": page_settings}
page_fn = pages.get(st.session_state.page, page_dashboard)
page_fn()
