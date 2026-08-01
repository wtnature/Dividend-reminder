"""
SQLite 数据库层 — 持仓、分红记录、轮询状态管理
"""

import sqlite3
import os
from datetime import datetime
from contextlib import contextmanager

DB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DB_PATH = os.path.join(DB_DIR, "dividend_reminder.db")


def ensure_db_dir():
    os.makedirs(DB_DIR, exist_ok=True)


@contextmanager
def get_connection():
    """获取数据库连接，自动提交/关闭"""
    ensure_db_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database():
    """初始化所有表（幂等操作）"""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL UNIQUE,
                stock_name TEXT NOT NULL,
                market TEXT NOT NULL CHECK(market IN ('A', 'HK')),
                shares_held REAL NOT NULL DEFAULT 0,
                tax_bracket TEXT NOT NULL DEFAULT 'over_1_year',
                holding_start_date TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS dividend_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                announcement_date TEXT,
                record_date TEXT,
                ex_dividend_date TEXT,
                payment_date TEXT,
                dividend_per_share REAL,
                dividend_currency TEXT DEFAULT 'CNY',
                status TEXT NOT NULL DEFAULT 'pending',
                data_source TEXT,
                is_manual INTEGER NOT NULL DEFAULT 0,
                date_conflict INTEGER NOT NULL DEFAULT 0,
                amount_missing INTEGER NOT NULL DEFAULT 0,
                net_amount_rmb REAL,
                next_payment_date TEXT,
                next_dividend_per_share REAL,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (stock_code) REFERENCES portfolios(stock_code)
            );

            CREATE TABLE IF NOT EXISTS polling_state (
                stock_code TEXT PRIMARY KEY,
                state TEXT NOT NULL DEFAULT 'not_announced',
                last_checked_at TEXT,
                next_check_at TEXT,
                FOREIGN KEY (stock_code) REFERENCES portfolios(stock_code)
            );
        """)


# ============================================================
#  Portfolio CRUD
# ============================================================
def add_stock(stock_code: str, stock_name: str, market: str,
              shares_held: float = 0, tax_bracket: str = "over_1_year",
              holding_start_date: str = None) -> bool:
    """添加新的监控标的"""
    with get_connection() as conn:
        try:
            conn.execute("""
                INSERT INTO portfolios (stock_code, stock_name, market, shares_held,
                    tax_bracket, holding_start_date)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (stock_code, stock_name, market, shares_held, tax_bracket, holding_start_date))
            # 同时初始化轮询状态
            conn.execute("""
                INSERT OR IGNORE INTO polling_state (stock_code, state)
                VALUES (?, 'not_announced')
            """, (stock_code,))
            return True
        except sqlite3.IntegrityError:
            return False


def update_stock(stock_code: str, **kwargs) -> bool:
    """更新持仓信息"""
    allowed = {"stock_name", "shares_held", "tax_bracket",
               "holding_start_date", "is_active"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [stock_code]
    with get_connection() as conn:
        conn.execute(f"UPDATE portfolios SET {set_clause} WHERE stock_code = ?", values)
        return conn.total_changes > 0


def delete_stock(stock_code: str):
    """删除标的及其关联数据"""
    with get_connection() as conn:
        conn.execute("DELETE FROM polling_state WHERE stock_code = ?", (stock_code,))
        conn.execute("DELETE FROM dividend_records WHERE stock_code = ?", (stock_code,))
        conn.execute("DELETE FROM portfolios WHERE stock_code = ?", (stock_code,))


def get_all_stocks(active_only: bool = True) -> list[dict]:
    """获取所有持仓"""
    with get_connection() as conn:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM portfolios WHERE is_active = 1 ORDER BY market, stock_code"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM portfolios ORDER BY market, stock_code"
            ).fetchall()
        return [dict(r) for r in rows]


def get_stock(stock_code: str) -> dict | None:
    """获取单只股票"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM portfolios WHERE stock_code = ?", (stock_code,)
        ).fetchone()
        return dict(row) if row else None


# ============================================================
#  Dividend Records CRUD
# ============================================================
def upsert_dividend_record(stock_code: str, announcement_date: str = None,
                           record_date: str = None, ex_dividend_date: str = None,
                           payment_date: str = None, dividend_per_share: float = None,
                           dividend_currency: str = "CNY", data_source: str = None,
                           is_manual: int = None, date_conflict: int = 0,
                           amount_missing: int = 0,
                           net_amount_rmb: float = None,
                           next_payment_date: str = None,
                           next_dividend_per_share: float = None,
                           notes: str = None) -> int:
    """插入或更新分红记录。每只股票只保留一条记录。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM dividend_records WHERE stock_code = ? ORDER BY updated_at DESC LIMIT 1",
            (stock_code,)
        ).fetchone()

        if existing:
            # Build SET clause dynamically
            set_parts = ["updated_at = ?"]
            values = [now]

            # Fields that should be set to NULL when None is passed
            nullable_cols = {"next_payment_date", "next_dividend_per_share"}

            for col, val in [
                ("announcement_date", announcement_date),
                ("record_date", record_date),
                ("ex_dividend_date", ex_dividend_date),
                ("payment_date", payment_date),
                ("dividend_per_share", dividend_per_share),
                ("dividend_currency", dividend_currency),
                ("data_source", data_source),
                ("net_amount_rmb", net_amount_rmb),
                ("next_payment_date", next_payment_date),
                ("next_dividend_per_share", next_dividend_per_share),
                ("notes", notes),
            ]:
                if val is not None or col in nullable_cols:
                    set_parts.append(f"{col} = ?")
                    values.append(val)

            if is_manual is not None:
                set_parts.append("is_manual = ?")
                values.append(is_manual)

            set_parts.append("date_conflict = ?")
            values.append(date_conflict)
            set_parts.append("amount_missing = ?")
            values.append(amount_missing)

            values.append(existing["id"])
            conn.execute(
                f"UPDATE dividend_records SET {', '.join(set_parts)} WHERE id = ?",
                values
            )
            return existing["id"]
        else:
            cursor = conn.execute("""
                INSERT INTO dividend_records
                    (stock_code, announcement_date, record_date,
                     ex_dividend_date, payment_date, dividend_per_share,
                     dividend_currency, data_source, is_manual, date_conflict,
                     amount_missing, net_amount_rmb,
                     next_payment_date, next_dividend_per_share, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (stock_code, announcement_date, record_date,
                  ex_dividend_date, payment_date, dividend_per_share,
                  dividend_currency, data_source, is_manual or 0, date_conflict,
                  amount_missing, net_amount_rmb,
                  next_payment_date, next_dividend_per_share, notes))
            return cursor.lastrowid


def get_dividend_record(stock_code: str) -> dict | None:
    """获取股票的分红记录"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM dividend_records WHERE stock_code = ? ORDER BY updated_at DESC LIMIT 1",
            (stock_code,)
        ).fetchone()
        return dict(row) if row else None


def get_dividends_needing_review() -> list[dict]:
    """获取所有需要人工确认的分红记录（日期冲突或金额缺失）"""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT p.stock_name, d.*
            FROM dividend_records d
            JOIN portfolios p ON d.stock_code = p.stock_code
            WHERE d.date_conflict = 1 OR d.amount_missing = 1
            ORDER BY d.updated_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_upcoming_payments(days: int = 30) -> list[dict]:
    """获取未来即将派息的记录（含自动和手动数据）"""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT p.stock_name, p.shares_held, p.tax_bracket, p.market, d.*,
                   COALESCE(d.next_payment_date, d.payment_date) as effective_payment_date,
                   COALESCE(d.next_dividend_per_share, d.dividend_per_share) as effective_dps
            FROM dividend_records d
            JOIN portfolios p ON d.stock_code = p.stock_code
            WHERE (d.payment_date IS NOT NULL OR d.next_payment_date IS NOT NULL)
              AND COALESCE(d.next_payment_date, d.payment_date) >= date('now','localtime')
              AND d.date_conflict = 0
            ORDER BY effective_payment_date ASC
        """).fetchall()
        return [dict(r) for r in rows]


# ============================================================
#  Polling State
# ============================================================
def get_polling_state(stock_code: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM polling_state WHERE stock_code = ?", (stock_code,)
        ).fetchone()
        return dict(row) if row else None


def update_polling_state(stock_code: str, state: str = None,
                         last_checked_at: str = None,
                         next_check_at: str = None):
    updates = {}
    if state is not None:
        updates["state"] = state
    if last_checked_at is not None:
        updates["last_checked_at"] = last_checked_at
    if next_check_at is not None:
        updates["next_check_at"] = next_check_at
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [stock_code]
    with get_connection() as conn:
        conn.execute(f"UPDATE polling_state SET {set_clause} WHERE stock_code = ?", values)


def get_all_polling_states() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT p.stock_name, p.market, ps.*
            FROM polling_state ps
            JOIN portfolios p ON ps.stock_code = p.stock_code
            WHERE p.is_active = 1
        """).fetchall()
        return [dict(r) for r in rows]


def get_stocks_due_for_check(current_time_str: str) -> list[dict]:
    """获取到期需要轮询的股票"""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT p.*, ps.state as poll_state, ps.last_checked_at
            FROM portfolios p
            JOIN polling_state ps ON p.stock_code = ps.stock_code
            WHERE p.is_active = 1
              AND (ps.next_check_at IS NULL OR ps.next_check_at <= ?)
              AND ps.state != 'payment_locked'
        """, (current_time_str,)).fetchall()
        return [dict(r) for r in rows]
