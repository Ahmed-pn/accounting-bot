"""
وحدة قاعدة البيانات - بوت المحاسبة (لأي محل تجاري)
كل تاجر/مستخدم عنده سجل عمليات منفصل حسب معرف تيليغرام تبعه (user_id)

الأنواع: sale (بيع), expense (مصروف عام), purchase (مشترى بضاعة)
"""
import sqlite3
from datetime import datetime, date, timedelta
from contextlib import contextmanager

DB_PATH = "accounting.db"


def init_db():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,              -- 'sale' / 'expense' / 'purchase'
                amount REAL NOT NULL,
                description TEXT,
                customer_name TEXT,              -- اختياري: اسم الزبون (يستخدم غالبًا مع البيع)
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS debts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                direction TEXT NOT NULL,         -- 'owed_to_me' أو 'i_owe'
                person_name TEXT NOT NULL,
                amount REAL NOT NULL,
                note TEXT,
                settled INTEGER DEFAULT 0,
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


# ---------- المعاملات (بيع / مصروف / مشترى) ----------

def add_transaction(user_id: int, tx_type: str, amount: float, description: str = "", customer_name: str = None):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO transactions (user_id, type, amount, description, customer_name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, tx_type, amount, description, customer_name, datetime.now().isoformat())
        )
        conn.commit()
        return cur.lastrowid


def delete_transaction(tx_id: int, user_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (tx_id, user_id))
        conn.commit()
        return cur.rowcount > 0


def get_balance(user_id: int, since: str = None):
    """يرجع (مبيعات، مصاريف، مشتريات، الصافي)"""
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
        purchases = rows.get("purchase", 0) or 0
        net = sales - expenses - purchases
        return sales, expenses, purchases, net


def get_transactions(user_id: int, tx_type: str = None, since: str = None, limit: int = 10):
    with get_conn() as conn:
        cur = conn.cursor()
        query = "SELECT id, type, amount, description, customer_name, created_at FROM transactions WHERE user_id = ?"
        params = [user_id]
        if tx_type:
            query += " AND type = ?"
            params.append(tx_type)
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cur.execute(query, params)
        return cur.fetchall()


def get_today_since_iso():
    return datetime.combine(date.today(), datetime.min.time()).isoformat()


def get_week_since_iso():
    today = date.today()
    start = today - timedelta(days=today.weekday())  # يبدأ الاثنين
    return datetime.combine(start, datetime.min.time()).isoformat()


def get_month_since_iso():
    today = date.today()
    return datetime(today.year, today.month, 1).isoformat()


def get_year_since_iso():
    today = date.today()
    return datetime(today.year, 1, 1).isoformat()


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
        cur.execute("UPDATE debts SET settled = 1 WHERE id = ? AND user_id = ?", (debt_id, user_id))
        conn.commit()
        return cur.rowcount > 0


def settle_debt_by_name(user_id: int, person_name: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, amount, direction FROM debts WHERE user_id = ? AND person_name = ? AND settled = 0 ORDER BY created_at DESC LIMIT 1",
            (user_id, person_name)
        )
        row = cur.fetchone()
        if not row:
            return False, None, None
        debt_id, amount, direction = row
        cur.execute("UPDATE debts SET settled = 1 WHERE id = ?", (debt_id,))
        conn.commit()
        return True, amount, direction
