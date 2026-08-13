"""
وحدة قاعدة البيانات - بوت المحاسبة
كل تاجر/مستخدم عنده سجل عمليات منفصل حسب معرف تيليغرام تبعه (user_id)
"""
import sqlite3
from datetime import datetime, date
from contextlib import contextmanager

DB_PATH = "accounting.db"


def init_db():
    """إنشاء الجداول إذا ما كانت موجودة"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,           -- 'sale' أو 'expense'
                amount REAL NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS debts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                direction TEXT NOT NULL,      -- 'owed_to_me' أو 'i_owe'
                person_name TEXT NOT NULL,
                amount REAL NOT NULL,
                note TEXT,
                settled INTEGER DEFAULT 0,    -- 0 = لسا مو متسدد, 1 = تسدد
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


# ---------- المبيعات والمصاريف ----------

def add_transaction(user_id: int, tx_type: str, amount: float, description: str = ""):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, tx_type, amount, description, datetime.now().isoformat())
        )
        conn.commit()
        return cur.lastrowid


def get_balance(user_id: int, since: str = None):
    """يرجع (مجموع المبيعات، مجموع المصاريف، الصافي)"""
    with get_conn() as conn:
        cur = conn.cursor()
        query = "SELECT type, SUM(amount) FROM transactions WHERE user_id = ?"
        params = [user_id]
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        query += " GROUP BY type"
        cur.execute(query, params)
        rows = dict(cur.fetchall())
        sales = rows.get("sale", 0) or 0
        expenses = rows.get("expense", 0) or 0
        return sales, expenses, sales - expenses


def get_recent_transactions(user_id: int, limit: int = 10):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT type, amount, description, created_at FROM transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        )
        return cur.fetchall()


def get_today_since_iso():
    return datetime.combine(date.today(), datetime.min.time()).isoformat()


def get_month_since_iso():
    today = date.today()
    return datetime(today.year, today.month, 1).isoformat()


# ---------- الديون ----------

def add_debt(user_id: int, direction: str, person_name: str, amount: float, note: str = ""):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO debts (user_id, direction, person_name, amount, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, direction, person_name, amount, note, datetime.now().isoformat())
        )
        conn.commit()
        return cur.lastrowid


def get_open_debts(user_id: int, direction: str = None):
    with get_conn() as conn:
        cur = conn.cursor()
        query = "SELECT id, direction, person_name, amount, note FROM debts WHERE user_id = ? AND settled = 0"
        params = [user_id]
        if direction:
            query += " AND direction = ?"
            params.append(direction)
        cur.execute(query, params)
        return cur.fetchall()


def settle_debt(debt_id: int, user_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE debts SET settled = 1 WHERE id = ? AND user_id = ?",
            (debt_id, user_id)
        )
        conn.commit()
        return cur.rowcount > 0
